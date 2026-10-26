#!/usr/bin/python3
"""Agent-requested finalization, executed by the same Runner after AI child exit."""
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from workspace_lifecycle import (Platform, REGISTRY, atomic_json, lease, project_gate,
                                 read_config, record_path)
from workspace_cleanup import clean_record, final_group
from workspace_archive import check_secret


def now():
    return datetime.now(timezone.utc).isoformat()


def agent_platform(config):
    if not os.environ.get('ITSAPLAN_API_KEY') or not os.environ.get('ITSAPLAN_URL'):
        raise RuntimeError('Finalize requires the current Runner-injected Agent identity')
    os.environ['ITSAPLAN_BASE_URL'] = os.environ['ITSAPLAN_URL']
    return Platform(config)


def post_comment(issue, text):
    result = subprocess.run(['itsaplan', 'issues', 'post-issues-by-issue-id-comments', str(issue),
                             '-o', 'json', '--rsh-print', 'b'], input=json.dumps({'body': text}),
                            env=dict(os.environ, ITSAPLAN_WRITE_MODE='write'),
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Agent cleanup report failed; inspect protected event receipt')


def request(main, reason):
    check_secret('finalization-reason', reason.encode())
    config = read_config()
    if config.get('enabled') is not True:
        raise RuntimeError('Agent finalization is explicitly disabled')
    platform = agent_platform(config)
    own_issue = int(os.environ['ITSAPLAN_ISSUE_ID'])
    run = os.environ['ITSAPLAN_RUN_ID']
    project = os.environ['ITSAPLAN_PROJECT_KEY']
    name = os.environ['ITSAPLAN_AGENT_NAME']
    if platform.root_issue(own_issue) != own_issue or main != own_issue:
        raise RuntimeError('Only the final main-task run may request group cleanup')
    for root, members in config.get('issueDependencies', {}).items():
        if own_issue in members:
            raise RuntimeError('A review dependency cannot finalize its main task')
    issue = platform.issue(main, fresh=True)
    data = platform.project(project)
    agents = [a for a in data['assignees'] if a.get('kind') == 'agent' and a.get('name') == name]
    if len(agents) != 1 or issue.get('delegateUserId') != agents[0]['userId']:
        raise RuntimeError('Current Agent is not the delegated final main-task owner')
    states = {c['id']: c['stateType'] for c in data['columns']}
    terminal = states.get(issue['columnId'])
    if terminal not in ('completed', 'canceled'):
        raise RuntimeError('Read back Done or Canceled before requesting finalize')
    if terminal == 'canceled' and not reason.strip():
        raise RuntimeError('Canceled finalization requires an archived cancellation reason')
    group_ids = set()
    pending = [main] + config.get('issueDependencies', {}).get(str(main), [])
    while pending:
        current = int(pending.pop())
        if current in group_ids:
            continue
        obj = platform.issue(current, fresh=True)
        if obj['projectId'] != data['project']['id'] or states.get(obj['columnId']) not in ('completed', 'canceled'):
            raise RuntimeError('Main task or review/verification dependency is still open')
        group_ids.add(current)
        pending += [s['id'] for s in obj.get('subtasks', [])]
    observed = []
    for item in platform.runs():
        if item.get('issueId') not in group_ids:
            continue
        if str(item['id']) != run and item['status'] in ('pending', 'running', 'claimed'):
            raise RuntimeError('Another related execution is active or queued; preserve worktrees')
        observed.append({'id': item['id'], 'issueId': item['issueId'], 'status': item['status'],
                         'agentId': item['_agentId']})
    registry = json.loads(REGISTRY.read_text())
    root = Path(registry[project]['worktreeRoot']) / ('run-' + run)
    cwd = Path.cwd().resolve()
    if cwd == root or root in cwd.parents:
        raise RuntimeError('Leave the task worktree before requesting finalize')
    folder = Path(os.environ['ITSAPLAN_FINALIZE_REQUEST_DIR'])
    token = os.environ['ITSAPLAN_FINALIZE_TOKEN']
    if not folder.is_dir() or folder.is_symlink():
        raise RuntimeError('Missing live Runner request channel')
    value = {'mainIssueId': main, 'issueId': own_issue, 'runId': run, 'project': project,
             'agentName': name, 'terminalState': terminal, 'reason': reason,
             'groupIssueIds': sorted(group_ids), 'observedRuns': observed,
             'requestedAt': now(), 'token': token}
    post_comment(main, '已由 Agent 主動呼叫 itsaplan-finalize，並讀回終態。清理請求已交給同一 run 的適配層；AI child 退出後才會重新檢查依賴、執行鎖及歸檔。此時尚未宣稱清理完成。')
    atomic_json(folder / 'request.json', value)
    return {'accepted': True, 'agent': name, 'runId': run, 'mainIssueId': main,
            'execution': 'same-run adapter after AI child exit', 'cleanupCompleted': False}


class Invocation:
    def __init__(self, config, project, run):
        self.config, self.project, self.run = config, project, run
        self.folder = Path(tempfile.mkdtemp(prefix='itsaplan-finalize-'))
        self.folder.chmod(0o700)
        self.token = secrets.token_urlsafe(32)
        os.environ['ITSAPLAN_FINALIZE_REQUEST_DIR'] = str(self.folder)
        os.environ['ITSAPLAN_FINALIZE_TOKEN'] = self.token

    def child_ended(self, exit_code):
        path = record_path(self.config, self.project, self.run)
        if path.exists():
            record = json.loads(path.read_text())
            record['childEndedAt'] = now()
            record['childExitCode'] = exit_code
            atomic_json(path, record)
        request_path = self.folder / 'request.json'
        if not request_path.exists():
            return None
        value = json.loads(request_path.read_text())
        if not secrets.compare_digest(value.pop('token', ''), self.token):
            raise RuntimeError('Finalize request capability mismatch')
        expected = (self.project, self.run, int(os.environ['ITSAPLAN_ISSUE_ID']), os.environ['ITSAPLAN_AGENT_NAME'])
        actual = (value['project'], value['runId'], value['issueId'], value['agentName'])
        if actual != expected or value['mainIssueId'] != value['issueId']:
            raise RuntimeError('Finalize request does not belong to this Runner invocation')
        record['agentFinalizeRequest'] = value
        atomic_json(path, record)
        if exit_code != 0:
            post_comment(value['mainIssueId'], 'Agent 已要求收尾清理，但 AI child 未成功退出；工作區及成果仍保留，清理未完成。')
            return None
        return value

    def finalize(self, request_value):
        # CLI reads and the final Agent report must not recreate a retired
        # workspace through the task-scoped XDG cache path.
        cache = self.folder / 'finalize-cli-cache'
        cache.mkdir(mode=0o700, exist_ok=True)
        os.environ['XDG_CACHE_HOME'] = str(cache)
        # One bounded settling delay belongs to this explicit invocation. There
        # is no recurring timer, queue consumer, sweep or retry job.
        config = read_config()
        time.sleep(max(0, config['quietSeconds']))
        config = read_config()
        if config.get('enabled') is not True:
            return self.report(request_value, [{'outcome': 'retained:disabled'}])
        if config['stateRoot'] != self.config['stateRoot']:
            return self.report(request_value, [{'outcome': 'retained:state-root-changed'}])
        registry = json.loads(REGISTRY.read_text())
        lock = Path(config['stateRoot']) / 'finalize.lock'
        lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.report(request_value, [{'outcome': 'retained:configuration-update-active'}])
            config = read_config()
            if config.get('enabled') is not True or config['stateRoot'] != self.config['stateRoot']:
                return self.report(request_value, [{'outcome': 'retained:configuration-changed'}])
            platform = agent_platform(config)
            own_path = record_path(config, self.project, self.run)
            own_record = json.loads(own_path.read_text())
            if own_record.get('childExitCode') != 0 or own_record.get('agentFinalizeRequest') != request_value:
                return self.report(request_value, [{'outcome': 'retained:child-completion-evidence-missing'}])
            agent_rows = platform.call('ai-agents', 'get-teams-by-team-id-ai-agents', config['teamId'])
            matches = [a for a in agent_rows if a['name'] == request_value['agentName']]
            if len(matches) != 1 or matches[0]['id'] not in config['agentIds']:
                return self.report(request_value, [{'outcome': 'retained:agent-evidence-missing'}])
            project_data = platform.project(self.project)
            main = platform.issue(request_value['mainIssueId'], fresh=True)
            identities = [a for a in project_data['assignees'] if a.get('kind') == 'agent' and a.get('name') == request_value['agentName']]
            states = {c['id']: c['stateType'] for c in project_data['columns']}
            if (len(identities) != 1 or main.get('delegateUserId') != identities[0]['userId']
                    or main['projectId'] != project_data['project']['id']
                    or states.get(main['columnId']) != request_value['terminalState']
                    or (request_value['terminalState'] == 'canceled' and not request_value['reason'].strip())):
                return self.report(request_value, [{'outcome': 'retained:main-owner-or-terminal-changed'}])
            official = [r for r in platform.runs() if str(r['id']) == self.run and r.get('issueId') == request_value['issueId'] and r['_agentId'] == matches[0]['id']]
            if len(official) != 1 or official[0]['status'] not in ('pending', 'running', 'claimed', 'success'):
                return self.report(request_value, [{'outcome': 'retained:official-current-run-conflict'}])
            completion = {'runId': self.run, 'issueId': request_value['issueId'],
                          'agentId': matches[0]['id'], 'exitCode': 0}
            group, reason = final_group(platform, self.project, request_value['mainIssueId'], config, completion)
            if reason or group['mainId'] != request_value['mainIssueId']:
                return self.report(request_value, [{'outcome': 'retained:' + (reason or 'main-task-changed')}])
            if group['issueIds'] != request_value['groupIssueIds']:
                return self.report(request_value, [{'outcome': 'retained:dependency-group-changed'}])
            observed = {(r['id'], r['issueId'], r['agentId']): r['status'] for r in request_value['observedRuns']}
            for item in platform.runs():
                if item.get('issueId') not in group['issueIds'] or str(item['id']) == self.run:
                    continue
                if observed.get((item['id'], item['issueId'], item['_agentId'])) != item['status']:
                    return self.report(request_value, [{'outcome': 'retained:related-runs-changed-since-request'}])
            outcomes = []
            with project_gate(config, self.project, exclusive=True) as acquired:
                if not acquired:
                    return self.report(request_value, [{'outcome': 'retained:project-execution-active'}])
                paths = sorted((Path(config['stateRoot']) / 'records' / self.project).glob('*.json'))
                for path in paths:
                    record = json.loads(path.read_text())
                    if record['issueId'] not in group['issueIds']:
                        continue
                    try:
                        outcome = clean_record(record, config, registry, platform, completion, project_locked=True)
                    except Exception as error:
                        outcome = 'retained:guard:' + str(error)
                    outcomes.append({'runId': record['runId'], 'outcome': outcome})
            return self.report(request_value, outcomes)

    def report(self, request_value, outcomes):
        value = {'requestedByAgent': request_value['agentName'], 'requestRunId': self.run,
                 'mainIssueId': request_value['mainIssueId'], 'trigger': 'agent-tool-request/AI-child-exit',
                 'time': now(), 'outcomes': outcomes}
        atomic_json(Path(self.config['stateRoot']) / 'finalize-events' / (self.run + '.json'), value)
        complete = bool(outcomes) and all(o['outcome'] in ('cleaned', 'already-cleaned') for o in outcomes)
        text = 'Agent 主動收尾清理已完成：已歸檔驗證、解除所屬 Git worktree 註冊及移除目錄，分支保留。' if complete else 'Agent 主動收尾清理未全部完成；未通過安全檢查的工作區及成果仍保留。'
        text += '\n' + json.dumps(outcomes, ensure_ascii=False)
        try:
            post_comment(request_value['mainIssueId'], text)
            value['reportDelivered'] = True
        except Exception:
            value['reportDelivered'] = False
            print('Finalization outcome persisted; Agent platform report unavailable.', file=sys.stderr)
        atomic_json(Path(self.config['stateRoot']) / 'finalize-events' / (self.run + '.json'), value)
        return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['request'])
    parser.add_argument('--main-issue', type=int, required=True)
    parser.add_argument('--reason', default='')
    args = parser.parse_args()
    try:
        print(json.dumps(request(args.main_issue, args.reason)))
    except Exception as error:
        parser.exit(1, 'Agent finalize: ' + str(error) + '\n')
