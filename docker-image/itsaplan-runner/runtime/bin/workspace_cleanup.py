#!/usr/bin/python3
"""Retire final, idle, archive-verified Runner workspaces using native Git."""
import argparse
import contextlib
import fcntl
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from workspace_archive import archive, inventory, verify_archive
from workspace_lifecycle import (CONFIG, REGISTRY, Platform, atomic_json, git,
                                 lease, project_gate, process_using, read_config, record_path,
                                 register, registered, validate, validate_config)


def completed(platform, project, issue):
    states = {c['id']: c['stateType'] for c in platform.project(project)['columns']}
    return states.get(issue['columnId']) == 'completed'


def final_group(platform, project, issue, config, completion=None):
    validate_config(config)
    main_id = platform.root_issue(issue)
    # Explicit links cover independent review work items without parentId.
    for root, dependencies in config.get('issueDependencies', {}).items():
        if main_id in dependencies:
            main_id = int(root)
    main = platform.issue(main_id, fresh=True)
    if main.get('parentId'):
        return None, 'dependency-main-is-not-root'
    for dependency in config.get('issueDependencies', {}).get(str(main_id), []):
        if platform.issue(dependency, fresh=True).get('parentId'):
            return None, 'independent-dependency-has-parent'
    states = {c['id']: c['stateType'] for c in platform.project(project)['columns']}
    canceled = states.get(main['columnId']) == 'canceled'
    if states.get(main['columnId']) not in ('completed', 'canceled'):
        return None, 'main-task-not-Done'
    since = datetime.fromisoformat(main['statusSince'].replace('Z', '+00:00'))
    if (datetime.now(timezone.utc) - since).total_seconds() < config['quietSeconds']:
        return None, 'completion-settle-period'
    pending = [main_id] + config.get('issueDependencies', {}).get(str(main_id), [])
    ids = set()
    timestamps = {}
    while pending:
        current = int(pending.pop())
        if current in ids:
            continue
        obj = platform.issue(current, fresh=True)
        if obj.get('projectId') != platform.project(project)['project']['id']:
            return None, 'dependency-project-mismatch'
        if states.get(obj['columnId']) not in ('completed', 'canceled'):
            return None, 'review-or-subtask-not-Done'
        completed_since = datetime.fromisoformat(obj['statusSince'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - completed_since).total_seconds() < config['quietSeconds']:
            return None, 'dependency-completion-settle-period'
        ids.add(current)
        timestamps[str(current)] = obj['statusSince']
        pending += [s['id'] for s in obj.get('subtasks', [])]
    latest = {}
    for run in platform.runs():
        if run.get('issueId') not in ids:
            continue
        own_ended = completion and str(run['id']) == completion['runId'] and run.get('_agentId') == completion['agentId'] and run['issueId'] == completion['issueId'] and completion.get('exitCode') == 0
        if run['status'] in ('pending', 'running', 'claimed') and not own_ended:
            return None, 'related-run-active-or-pending'
        if own_ended and run['status'] in ('pending', 'running', 'claimed'):
            run = dict(run, status='success')
        key = (run['issueId'], run.get('_agentId', run.get('agentId')))
        if key not in latest or run['id'] > latest[key]['id']:
            latest[key] = run
    if ids - {key[0] for key in latest}:
        return None, 'related-run-evidence-missing'
    allowed = ('success', 'failed', 'canceled') if canceled else ('success',)
    if any(r['status'] not in allowed for r in latest.values()):
        return None, 'latest-related-run-not-successful'
    return {'mainId': main_id, 'issueIds': sorted(ids), 'statusSince': main['statusSince'],
            'issueStatusSince': timestamps, 'mainDelegateUserId': main.get('delegateUserId')}, None


def adopt_legacy(config, registry, platform):
    runs = {str(r['id']): r for r in platform.runs()}
    count = 0
    for key, project in registry.items():
        base = Path(project['worktreeRoot'])
        if not base.exists():
            continue
        for root in sorted(base.iterdir()):
            if not root.name.startswith('run-') or not root.is_dir() or root.is_symlink():
                continue
            run = root.name.removeprefix('run-')
            path = record_path(config, key, run)
            if path.exists() or run not in runs or not runs[run].get('issueId'):
                continue
            with lease(config, key, run, exclusive=True) as acquired:
                if not acquired:
                    continue
                manifest = json.loads((root / 'workspace.json').read_text())
                if str(manifest['runId']) != run or manifest['project'] != key:
                    raise RuntimeError('Legacy manifest identity mismatch')
                # Source mapping, actual registered checkout, branch and official
                # platform run association all must agree before adoption.
                value = register(config, registry, key, run, runs[run]['issueId'], root, manifest['repositories'])
                value['adoptedLegacy'] = True
                atomic_json(path, value)
                count += 1
    return count


def container_remove(record, config, registry):
    """Remove one owned container via a detached control Git worktree.

    No recursive filesystem delete or scripted file-deletion loop is used.
    A same-filesystem rename moves already archived remaining artifacts into a
    disposable, precisely registered Git worktree before native removal.
    """
    root = Path(record['root'])
    control = Path(config['stateRoot']) / 'container-control.git'
    staging = Path(registry[record['project']]['worktreeRoot']) / '.retirement' / ('run-' + record['runId'])
    if root.is_symlink() or staging.is_symlink():
        raise RuntimeError('Unsafe task-container retirement path')
    if not root.exists() and not staging.exists():
        return
    if not control.exists():
        subprocess.run(['/usr/bin/git', 'init', '--bare', str(control)], capture_output=True, check=True)
        git(control, 'config', 'user.name', 'Itsaplan workspace lifecycle')
        git(control, 'config', 'user.email', 'workspace-lifecycle@localhost')
        tree = subprocess.run(['/usr/bin/git', '--git-dir', str(control), 'hash-object', '-t', 'tree', '--stdin', '-w'],
                              input=b'', capture_output=True, check=True).stdout.decode().strip()
        commit = subprocess.run(['/usr/bin/git', '--git-dir', str(control), 'commit-tree', tree],
                                input=b'Empty lifecycle container\n', capture_output=True, check=True).stdout.decode().strip()
        git(control, 'update-ref', 'HEAD', commit)
    if not staging.exists():
        staging.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        git(control, 'worktree', 'add', '--detach', str(staging), 'HEAD')
    if not registered(control, staging):
        raise RuntimeError('Retirement container is not registered to lifecycle control Git')
    record['containerStaging'] = str(staging)
    atomic_json(record_path(config, record['project'], record['runId']), record)
    payload = staging / 'payload'
    if root.exists():
        if payload.exists():
            raise RuntimeError('Two retirement payloads exist; preserve both')
        os.rename(root, payload)
    if process_using(staging):
        raise RuntimeError('Retirement container is in use')
    git(control, 'worktree', 'remove', '--force', str(staging))
    if root.exists() or staging.exists() or registered(control, staging):
        raise RuntimeError('Task-container retirement did not complete')


def clean_record(record, config, registry, platform, completion=None, project_locked=False):
    if config.get('enabled') is not True:
        return 'retained:disabled'
    if config.get('_configPath') and read_config() != config:
        return 'retained:configuration-changed'
    path = record_path(config, record['project'], record['runId'])
    with contextlib.ExitStack() as locks:
        gated = locks.enter_context(contextlib.nullcontext(True) if project_locked else project_gate(config, record['project'], exclusive=True))
        if not gated:
            return 'retained:project-execution-active'
        acquired = locks.enter_context(lease(config, record['project'], record['runId'], exclusive=True))
        if not acquired:
            return 'retained:execution-lock'
        record = json.loads(path.read_text())
        if record.get('cleaned'):
            return 'already-cleaned'
        if record['issueId'] in config.get('retainedIssueIds', []):
            return 'retained:explicit-legacy-protection'
        group, reason = final_group(platform, record['project'], record['issueId'], config, completion)
        if reason:
            return 'retained:' + reason
        official = [r for r in platform.runs() if str(r['id']) == record['runId'] and r.get('issueId') == record['issueId']]
        if len(official) != 1:
            return 'retained:owned-run-evidence-missing'
        # Locks and process probes cover every mapped implementation/review run.
        for other_path in (Path(config['stateRoot']) / 'records' / record['project']).glob('*.json'):
            other = json.loads(other_path.read_text())
            if other.get('cleaned') or other['issueId'] not in group['issueIds']:
                continue
            if process_using(other['root']):
                return 'retained:related-process-active'
            if other['runId'] != record['runId']:
                idle = locks.enter_context(lease(config, other['project'], other['runId'], exclusive=True))
                if not idle:
                    return 'retained:related-execution-lock'
        validate(record, registry, allow_missing=True)
        root = Path(record['root'])
        if root.exists():
            snapshot, fingerprint = inventory(record)
            destination = archive(record, config, snapshot, fingerprint)
            if inventory(record)[1] != fingerprint:
                return 'retained:workspace-changed-during-archive'
            record.setdefault('archives', [])
            if str(destination) not in record['archives']:
                record['archives'].append(str(destination))
            atomic_json(path, record)
        elif not record.get('archives'):
            raise RuntimeError('Missing workspace has no verified archive')
        for archived in record['archives']:
            verify_archive(archived)
        # Re-fetch queue state after archiving instead of trusting the initial
        # poll snapshot. All already mapped execution leases remain held.
        platform.run_cache = None
        platform.issue_cache = {}
        platform.project_cache = {}
        if config.get('_configPath') and (read_config() != config or json.loads(REGISTRY.read_text()) != registry):
            return 'retained:configuration-changed'
        fresh, reason = final_group(platform, record['project'], record['issueId'], config, completion)
        if reason or fresh != group:
            return 'retained:final-state-changed'
        if process_using(root):
            return 'retained:process-started'
        # Deepest-first handles a separately registered linked child nested in
        # its parent's checkout. Ordinary submodules were also archived above.
        for repo in sorted(record['repositories'], key=lambda r: len(Path(r['path']).parts), reverse=True):
            checkout = Path(repo['path'])
            if not checkout.exists() and not registered(repo['source'], checkout):
                continue
            # Force is permitted only after ownership and durable full snapshot
            # verification, including dirty/untracked/submodule artifacts.
            git(repo['source'], 'worktree', 'remove', '--force', '--force', str(checkout))
            if checkout.exists() or registered(repo['source'], checkout):
                raise RuntimeError('Git worktree removal did not complete')
            record.setdefault('removedRepositories', [])
            if str(checkout) not in record['removedRepositories']:
                record['removedRepositories'].append(str(checkout))
            atomic_json(path, record)
        container_remove(record, config, registry)
        record['cleaned'] = True
        record['completedMainIssueId'] = group['mainId']
        record['cleanedAt'] = datetime.now(timezone.utc).isoformat()
        atomic_json(path, record)
        return 'cleaned'


def sweep(config=None, registry=None, platform=None, adopt=True):
    live_config = config is None
    config = config or read_config()
    validate_config(config)
    registry = registry or json.loads(REGISTRY.read_text())
    platform = platform or Platform(config)
    if config.get('enabled') is not True:
        print(json.dumps({'outcome': 'disabled'}))
        return [{'outcome': 'disabled'}]
    lock = Path(config['stateRoot']) / 'finalize.lock'
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return [{'outcome': 'retained:another-cleanup-active'}]
        # Dependency registration takes this same lock. Refresh after acquiring
        # it, so a sweep queued before registration cannot use a stale map.
        if live_config:
            fresh = read_config()
            if fresh['stateRoot'] != config['stateRoot']:
                raise RuntimeError('Cleanup state root changed during sweep')
            config = fresh
            platform = Platform(config)
            if config.get('enabled') is not True:
                return [{'outcome': 'disabled'}]
        return _sweep(config, registry, platform, adopt)


def _sweep(config, registry, platform, adopt):
    if adopt:
        adopt_legacy(config, registry, platform)
    events = []
    records = Path(config['stateRoot']) / 'records'
    for path in sorted(records.glob('*/*.json')):
        record = json.loads(path.read_text())
        if record.get('cleaned'):
            continue
        try:
            outcome = clean_record(record, config, registry, platform)
        except Exception as e:
            message = str(e)
            outcome = ('retained:archive-guard:' if 'preserve workspace' in message else 'error:') + message
        event = {'project': record['project'], 'runId': record['runId'], 'issueId': record['issueId'],
                 'outcome': outcome, 'time': datetime.now(timezone.utc).isoformat()}
        events.append(event)
        print(json.dumps(event), flush=True)
    log = Path(config['stateRoot']) / 'events.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with log.open('a') as f:
        for event in events:
            f.write(json.dumps(event) + '\n')
        f.flush()
        os.fsync(f.fileno())
    return events


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['adopt'])
    args = parser.parse_args()
    if args.operation == 'adopt':
        print('Adopted:', adopt_legacy(read_config(), json.loads(REGISTRY.read_text()), Platform(read_config())))
