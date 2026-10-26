"""Ownership records and execution locks for Runner-created workspaces."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

CONFIG = Path(os.environ.get('ITSAPLAN_WORKSPACE_CLEANUP_CONFIG', '/code/itsaplan/config/cleanup.json'))
REGISTRY = Path(os.environ.get('ITSAPLAN_WORKSPACE_REGISTRY', '/code/itsaplan/config/projects.json'))


def validate_config(config):
    dependencies = config.get('issueDependencies', {})
    members = set()
    roots = set()
    for root, children in dependencies.items():
        if not re.fullmatch(r'[1-9][0-9]*', root) or not isinstance(children, list):
            raise RuntimeError('Invalid dependency mapping')
        roots.add(int(root))
        for child in children:
            if type(child) is not int or child < 1 or child in members:
                raise RuntimeError('Dependency must belong to one main task')
            members.add(child)
    if roots & members:
        raise RuntimeError('Dependency mappings must be flat and acyclic')
    return config


def read_config():
    value = validate_config(json.loads(CONFIG.read_text()))
    value['_configPath'] = str(CONFIG)
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
        temp = Path(f.name)
    temp.chmod(0o600)
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def git(path, *args, check=True, binary=False):
    r = subprocess.run(['/usr/bin/git', '-C', str(path), *args],
                       capture_output=True, text=not binary, timeout=300)
    if check and r.returncode:
        raise RuntimeError('Git operation failed: ' + args[0])
    return r.stdout if binary else r.stdout.strip()


def record_path(config, project, run):
    if not re.fullmatch(r'[A-Z][A-Z0-9]{0,9}', project) or not re.fullmatch(r'[1-9][0-9]*', str(run)):
        raise RuntimeError('Invalid workspace identity')
    return Path(config['stateRoot']) / 'records' / project / (str(run) + '.json')


@contextlib.contextmanager
def lease(config, project, run, exclusive=False):
    p = record_path(config, project, run)
    lock = Path(config['stateRoot']) / 'locks' / project / (str(run) + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open('a') as f:
        try:
            fcntl.flock(f, (fcntl.LOCK_EX | fcntl.LOCK_NB) if exclusive else fcntl.LOCK_SH)
        except BlockingIOError:
            yield False
            return
        # A retired execution must not recreate its retained branch implicitly.
        if not exclusive and p.exists() and json.loads(p.read_text()).get('cleaned'):
            raise RuntimeError('Execution workspace was retired; assign a new run for reopened work')
        yield True


@contextlib.contextmanager
def project_gate(config, project, exclusive=False):
    record_path(config, project, '1')
    lock = Path(config['stateRoot']) / 'locks' / project / 'project.lock'
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open('a') as f:
        try:
            fcntl.flock(f, (fcntl.LOCK_EX | fcntl.LOCK_NB) if exclusive else fcntl.LOCK_SH)
        except BlockingIOError:
            yield False
            return
        yield True


def registered(source, checkout):
    raw = git(source, 'worktree', 'list', '--porcelain', '-z', binary=True)
    needle = b'worktree ' + os.fsencode(checkout)
    return needle in raw.split(b'\0')


def registered_paths(source):
    raw = git(source, 'worktree', 'list', '--porcelain', '-z', binary=True)
    return [Path(os.fsdecode(part[len(b'worktree '):])) for part in raw.split(b'\0')
            if part.startswith(b'worktree ')]


def validate(record, registry, allow_missing=False):
    project = registry[record['project']]
    root = Path(record['root'])
    expected = Path(project['worktreeRoot']) / ('run-' + str(record['runId']))
    if root != expected or root.is_symlink() or root.resolve() != expected.absolute():
        raise RuntimeError('Workspace container is not the registered task container')
    configured = {Path(r['path']).resolve(): r for r in project['repositories']}
    if not record['repositories']:
        raise RuntimeError('Workspace has no repositories')
    owned_paths = {Path(r['path']) for r in record['repositories']}
    for source in configured:
        for checkout in registered_paths(source):
            if (checkout == root or root in checkout.parents) and checkout not in owned_paths:
                raise RuntimeError('Unowned nested linked worktree; preserve container')
    for repo in record['repositories']:
        source, checkout = Path(repo['source']), Path(repo['path'])
        if source.resolve() not in configured or source.is_symlink():
            raise RuntimeError('Unknown source repository')
        resolved = checkout.resolve()
        if (checkout != root and root not in checkout.parents) or resolved != checkout.absolute():
            raise RuntimeError('Checkout lies outside task container or traverses a symlink')
        if resolved != root.resolve() and root.resolve() not in resolved.parents:
            raise RuntimeError('Resolved checkout lies outside task container')
        if checkout.is_symlink() or checkout == source or checkout.resolve() == source.resolve():
            raise RuntimeError('Unsafe checkout path')
        if git(source, 'remote', 'get-url', 'origin') != configured[source.resolve()]['origin']:
            raise RuntimeError('Source repository origin changed')
        common = Path(git(source, 'rev-parse', '--absolute-git-dir')).resolve()
        if repo.get('commonGitDir') != str(common):
            raise RuntimeError('Source Git metadata changed')
        if not checkout.exists():
            if not allow_missing or registered(source, checkout):
                raise RuntimeError('Missing checkout still registered or not expected')
            continue
        if not registered(source, checkout):
            raise RuntimeError('Checkout is not a registered linked worktree')
        actual = Path(git(checkout, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if actual != common or git(checkout, 'branch', '--show-current') != repo['branch']:
            raise RuntimeError('Checkout metadata or branch does not match ownership record')


def register(config, registry, project, run, issue, root, repos):
    path = record_path(config, project, run)
    prior = json.loads(path.read_text()) if path.exists() else {}
    value = dict(prior, owner='itsaplan-runner', version=2, project=project,
                 runId=str(run), issueId=int(issue), root=str(root), repositories=[])
    if prior and any(prior.get(k) != value[k] for k in ('project', 'runId', 'issueId', 'root')):
        raise RuntimeError('Execution ownership changed')
    for repo in repos:
        value['repositories'].append(dict(repo, commonGitDir=str(
            Path(git(repo['source'], 'rev-parse', '--absolute-git-dir')).resolve())))
    validate(value, registry)
    atomic_json(path, value)
    # The protected record is authoritative; the visible manifest aids recovery.
    manifest = Path(root) / 'workspace.json'
    current = json.loads(manifest.read_text())
    current.update(owner=value['owner'], version=2, issueId=value['issueId'])
    atomic_json(manifest, current)
    return value


def process_using(root):
    root = Path(root).absolute()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        links = [proc / 'cwd']
        try:
            links += list((proc / 'fd').iterdir())
        except (FileNotFoundError, PermissionError):
            pass
        for link in links:
            try:
                target = Path(os.readlink(link).removesuffix(' (deleted)'))
                if target == root or root in target.parents:
                    return True
            except (FileNotFoundError, PermissionError, OSError):
                continue
    return False


def cli_failure(operation, result):
    # Report only known CLI diagnostics, never arbitrary response bodies or credentials.
    raw = (result.stderr + '\n' + result.stdout).lower()
    phrases = ('spec discovery failed', 'context deadline exceeded', 'permission denied',
               'connection refused', 'timed out', 'unauthorized', 'forbidden')
    details = '; '.join(text for text in phrases if text in raw) or 'details withheld'
    return RuntimeError(f'Platform CLI failed: {operation}; exit={result.returncode}; stderr={details}')


class Platform:
    def __init__(self, config):
        self.config = config
        self.issue_cache = {}
        self.project_cache = {}
        self.run_cache = None

    def call(self, group, operation, *args):
        for attempt in range(3):
            command = ['itsaplan', group, operation, *map(str, args),
                       '-o', 'json', '--rsh-print', 'b']
            try:
                r = subprocess.run(command, capture_output=True, text=True, timeout=90)
            except subprocess.TimeoutExpired as error:
                def text(value):
                    return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
                r = subprocess.CompletedProcess(command, 124, text(error.output),
                                                text(error.stderr) + '\ntimed out')
            if not r.returncode:
                return json.loads(r.stdout)
            raw = (r.stderr + '\n' + r.stdout).lower()
            transient = any(text in raw for text in ('spec discovery failed',
                            'context deadline exceeded', 'connection refused', 'timed out'))
            auth_error = any(text in raw for text in ('unauthorized', 'forbidden',
                             'permission denied', 'invalid api key')) or bool(
                                 re.search(r'\b(?:401|403)\b', raw))
            if not operation.startswith('get-') or not transient or auth_error or attempt == 2:
                raise cli_failure(operation, r)
            # Retry idempotent reads only, within this invocation. Keep raw
            # diagnostics private; they may contain credentials or request data.
            print(f'Bounded CLI read retry {attempt + 1}/2: {operation} (exit={r.returncode})',
                  file=sys.stderr)
            time.sleep(5)

    def issue(self, issue, fresh=False):
        if fresh or issue not in self.issue_cache:
            self.issue_cache[issue] = self.call('issues', 'get-issues-by-issue-id', issue)
        return self.issue_cache[issue]

    def project(self, key):
        if key not in self.project_cache:
            self.project_cache[key] = self.call('projects', 'get-projects-by-project-key', key)
        return self.project_cache[key]

    def root_issue(self, issue):
        seen = set()
        while True:
            if issue in seen:
                raise RuntimeError('Task parent cycle')
            seen.add(issue)
            obj = self.issue(issue, fresh=True)
            parent = obj.get('parentId')
            if not parent:
                return issue
            issue = int(parent)

    def runs(self):
        if self.run_cache is None:
            self.run_cache = []
            for agent in self.config['agentIds']:
                before = None
                seen = set()
                while True:
                    args = [self.config['teamId'], agent, '--limit', 100]
                    if before is not None:
                        args += ['--before', before]
                    data = self.call('ai-agents', 'get-teams-by-team-id-ai-agents-by-agent-id-runs', *args)
                    self.run_cache.extend(dict(r, _agentId=agent) for r in data['items'])
                    before = data.get('nextCursor')
                    if before is None:
                        break
                    if before in seen:
                        raise RuntimeError('Run pagination did not advance')
                    seen.add(before)
        return self.run_cache
