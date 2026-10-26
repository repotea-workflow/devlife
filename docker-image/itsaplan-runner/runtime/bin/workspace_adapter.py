"""Project routing and retry-safe linked worktrees for the official Runner."""
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from workspace_lifecycle import lease, project_gate, read_config, register
from workspace_finalize import Invocation

REGISTRY = Path(os.environ.get('ITSAPLAN_WORKSPACE_REGISTRY', '/code/itsaplan/config/projects.json'))
IDENTITY_KEYS = ('user.name', 'user.email', 'user.signingkey', 'core.sshCommand',
                 'gpg.format', 'commit.gpgsign', 'tag.gpgsign',
                 'credential.helper', 'credential.useHttpPath')


def git(path, *args, optional=False):
    result = subprocess.run(['/usr/bin/git', '-C', str(path), *args],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        if optional and result.returncode == 1:
            return None
        raise RuntimeError(f'Git operation failed in {path}: {args[0]}')
    return result.stdout.strip()


def identity(path):
    return {key: git(path, 'config', '--get', key, optional=True) for key in IDENTITY_KEYS}


def verify_checkout(source, checkout):
    common = git(checkout, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if Path(common).resolve() != Path(git(source, 'rev-parse', '--absolute-git-dir')).resolve():
        raise RuntimeError(f'Unexpected Git metadata for {checkout}')
    expected = identity(source)
    if not expected['user.name'] or not expected['user.email'] or identity(checkout) != expected:
        raise RuntimeError(f'Git identity routing differs for {checkout}')


def save_manifest(root, data):
    with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
        name = handle.name
    os.chmod(name, 0o600)
    os.replace(name, root / 'workspace.json')


def task_branch(key, project, run_id):
    if not re.fullmatch(r'[1-9][0-9]{0,18}', run_id):
        raise RuntimeError('Invalid run ID')
    users = {git(repo['path'], 'config', '--get', 'user.name')
             for repo in project['repositories']}
    if len(users) != 1 or not next(iter(users)):
        raise RuntimeError('Project repositories need one consistent Git user for branch naming')
    branch = f'{next(iter(users))}/{key.lower()}-{run_id}'
    result = subprocess.run(['/usr/bin/git', 'check-ref-format', '--branch', branch],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Git user cannot be used as a branch prefix')
    return branch


def allocate(key, project, run_id):
    if not re.fullmatch(r'[1-9][0-9]{0,18}', run_id):
        raise RuntimeError('Invalid run ID')
    base = Path(project['worktreeRoot'])
    base.mkdir(parents=True, exist_ok=True)
    locks = base / '.locks'
    locks.mkdir(exist_ok=True)
    root = base / f'run-{run_id}'
    branch = task_branch(key, project, run_id)
    with (locks / f'run-{run_id}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if root.is_symlink():
            raise RuntimeError('Symlink workspace is not allowed')
        root.mkdir(exist_ok=True)
        manifest = {'project': key, 'runId': run_id, 'branch': branch, 'repositories': []}
        manifest_path = root / 'workspace.json'
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        if previous and any(previous.get(k) != manifest[k] for k in ('project', 'runId', 'branch')):
            raise RuntimeError('Existing workspace belongs to another run')
        for repo in project['repositories']:
            source = Path(repo['path'])
            checkout = root / repo['name']
            if checkout.is_symlink() or git(source, 'remote', 'get-url', 'origin') != repo['origin']:
                raise RuntimeError(f'Unexpected repository mapping: {repo["name"]}')
            if not (checkout / '.git').exists():
                exists = subprocess.run(['/usr/bin/git', '-C', str(source), 'show-ref',
                                         '--verify', '--quiet', f'refs/heads/{branch}']).returncode
                if exists != 1:
                    raise RuntimeError(f'Branch already exists or cannot be checked: {branch}')
                base_ref = 'HEAD'
                if repo.get('parent'):
                    base_ref = git(root / repo['parent'], 'rev-parse',
                                   'HEAD:' + repo['submodule'])
                git(source, 'worktree', 'add', '-b', branch, str(checkout), base_ref)
            verify_checkout(source, checkout)
            if git(checkout, 'branch', '--show-current') != branch:
                raise RuntimeError(f'Unexpected branch in {checkout}')
            old = next((r for r in (previous or {}).get('repositories', [])
                        if r['name'] == repo['name']), None)
            record = {'name': repo['name'], 'source': str(source), 'path': str(checkout),
                      'branch': branch, 'baseCommit': old['baseCommit'] if old else git(checkout, 'rev-parse', 'HEAD')}
            if old and any(old.get(k) != record[k] for k in ('source', 'path', 'branch')):
                raise RuntimeError('Workspace repository mapping has changed')
            manifest['repositories'].append(record)
            # Keep earlier base commits for remaining repositories if a retry
            # stops halfway through updating an existing manifest.
            completed = {r['name'] for r in manifest['repositories']}
            pending = [r for r in (previous or {}).get('repositories', [])
                       if r['name'] not in completed]
            save_manifest(root, {**manifest, 'repositories': manifest['repositories'] + pending})
        return root, manifest['repositories']


def prepare():
    key = os.environ['ITSAPLAN_PROJECT_KEY']
    project = json.loads(REGISTRY.read_text())[key]
    system = os.environ.get('ITSAPLAN_SYSTEM_PROMPT', '')
    match = re.search(r'^You are working in the project ".*" \(key ([^)]+)\)', system, re.MULTILINE)
    chat = os.environ.get('ITSAPLAN_TRIGGER') == 'chat'
    if chat:
        # Resumed native sessions intentionally receive no system prompt. Bind
        # their trusted thread IDs on the initial invocation, then check the map.
        state = Path('/root/.config/itsaplan-runner/project-threads.json')
        thread = os.environ.get('ITSAPLAN_THREAD_ID', '')
        if not thread:
            raise RuntimeError('Missing chat thread ID')
        with state.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            threads = json.loads(state.read_text()) if state.exists() else {}
            if match and match.group(1) == key:
                if thread in threads and threads[thread] != key:
                    raise RuntimeError('Chat thread workspace changed')
                threads[thread] = key
                with tempfile.NamedTemporaryFile(mode='w', dir=state.parent, delete=False) as handle:
                    json.dump(threads, handle)
                    temporary = handle.name
                os.chmod(temporary, 0o600)
                os.replace(temporary, state)
            elif system or not os.environ.get('ITSAPLAN_SESSION_ID') or threads.get(thread) != key:
                raise RuntimeError('Unknown resumed chat workspace')
    elif not match or match.group(1) != key:
        raise RuntimeError('Project context does not match this Runner workspace')
    issue = os.environ.get('ITSAPLAN_ISSUE', '')
    if issue and issue.rsplit('-', 1)[0] != key:
        raise RuntimeError('Issue belongs to another project')
    run_id = os.environ.get('ITSAPLAN_RUN_ID')
    if run_id:
        issue_id = os.environ.get('ITSAPLAN_ISSUE_ID')
        if not issue_id or not issue_id.isdigit():
            raise RuntimeError('Missing official issue ID for workspace ownership')
        cwd, repos = allocate(key, project, run_id)
        register(read_config(), json.loads(REGISTRY.read_text()), key, run_id, issue_id, cwd, repos)
        note = ('This queued task has its own linked Git worktrees. Make code changes only in these '
                'checkouts, never the source checkouts. Keep existing changes on retries. '
                'Preserve worktrees while implementation, review or finalization remains open. '
                'At final main-task Done/Canceled, read back terminal state and actively invoke itsaplan-finalize from /opt/itsaplan-runner. '
                'Cleanup occurs in this same adapter after your AI child exits; no request means no cleanup. '
                'Do not push, merge, deploy or delegate unless explicitly requested. ')
        os.environ['XDG_CACHE_HOME'] = str(cwd / '.cache')
    elif chat:
        cwd = Path(project['cwd'])
        repos = [{'name': r['name'], 'path': r['path'], 'source': r['path']} for r in project['repositories']]
        note = ('This is the original project workspace for chat. Use queued issues for code changes '
                'requiring isolated worktrees. Do not push, merge, deploy or delegate unless requested. ')
    else:
        raise RuntimeError('Unsupported Runner invocation')
    if chat:
        # Keep platform CLI cache writes outside the read-only home/profile tree.
        cache = Path('/tmp/itsaplan-runner-cli-cache') / os.environ.get('ITSAPLAN_AGENT_NAME', 'chat')
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ['XDG_CACHE_HOME'] = str(cache)
    if run_id:
        note += f'\nUse the existing task branch {repos[0]["branch"]}; do not rename it or create another branch unless requested.\n'
    note += (f'\nProject: {key}\nActual working directory: {cwd}\nRepositories:\n' +
             '\n'.join(f'- {r["name"]}: {r["path"]}' for r in repos) +
             '\nUse the itsaplan CLI for platform operations. Never read or disclose credentials.\n')
    note += "\n## Language and platform task lifecycle\n- Use English for all GitHub-facing communication and authored content, including PR titles and bodies, issue titles and bodies, comments, review feedback, commit messages, and code comments. Use Traditional Chinese (zh-TW) for itsaplan human-facing communication, task progress comments, final reports, and direct user communication. Internal reasoning may use English. Preserve existing identifiers, commands, and proper nouns.\n- The installed `itsaplan` CLI is available for direct platform interaction using your own injected agent credentials. Use it yourself to read task context, post progress/blocker comments, and update task status; do not rely on the supervising assistant to make these changes. Never read or disclose credential files or keys.\n- Use command `--help` to discover the API-shaped CLI commands and `-o json --rsh-print b` for JSON bodies. For authorized writes, set `ITSAPLAN_WRITE_MODE=write` on the CLI invocation. Discover the current project's columns; do not assume column IDs are universal.\n- When actually starting an assigned task, move it to the project's In Progress state using the CLI and verify the result. Report meaningful progress and blockers under your own agent identity. Only move it to Done after every acceptance requirement is satisfied (including PR merge if requested), then read back the status and report evidence. A completed execution phase or a blocked run is not task completion.\n- For ordinary chat without an assigned task, do not invent a task or change unrelated task states. These rules grant no additional permission to change global settings, administer the platform, push, merge, deploy, or delegate; follow the explicit task authorization.\n"
    collaboration = json.loads(Path(os.environ.get('ITSAPLAN_COLLABORATION_CONFIG', '/code/itsaplan/config/collaboration.json')).read_text())
    agent_name = os.environ.get('ITSAPLAN_AGENT_NAME', '')
    if agent_name not in collaboration['agents']:
        raise RuntimeError('Missing configured collaboration identity')
    note += '\nAgent identity: ' + agent_name + '\n' + collaboration['common'] + '\n' + collaboration['agents'][agent_name] + '\n'
    return cwd, repos, note




def run(engine, args):
    cwd, repos, note = prepare()
    # Standalone gh must use the same account as the canonical Git helper.
    helper = git(repos[0]['source'], 'config', '--get-urlmatch',
                 'credential.helper', 'https://github.com', optional=True)
    for token in shlex.split((helper or '').replace('\n', ' ')):
        token = token.removeprefix('!')
        if token.startswith('GH_CONFIG_DIR='):
            os.environ['GH_CONFIG_DIR'] = token.split('=', 1)[1]
            break
    if engine == 'codex':
        # Replace the preset's workspace-write mode with the user's requested
        # unrestricted execution, retaining the model and session arguments.
        cleaned = []
        index = 0
        while index < len(args):
            if args[index:index + 2] == ['-c', 'sandbox_mode="workspace-write"']:
                index += 2
                continue
            cleaned.append(args[index])
            index += 1
        # Keep all -c overrides in the exec subcommand's scope. Root-scope
        # overrides are replaced when exec also receives -c (e.g. reasoning).
        option_start = cleaned.index('--json') if '--json' in cleaned else len(cleaned)
        argv = ['/opt/itsaplan-runner/bin/codex', *cleaned[:option_start],
                '-c', 'sandbox_mode="danger-full-access"', '-c', 'approval_policy="never"',
                *cleaned[option_start:]]
        child_env = dict(os.environ)
        helper = git(repos[0]['source'], 'config', '--get-urlmatch', 'credential.helper',
                     'https://github.com', optional=True)
        if helper and '/home/linuxbrew/.linuxbrew/bin/gh' in helper:
            # Preserve the repository's canonical account helper, replacing only
            # the executable path: sandboxed root cannot traverse Homebrew's
            # owner-only parent directory after dropping capabilities.
            helper = helper.replace('/home/linuxbrew/.linuxbrew/bin/gh',
                                    '/opt/itsaplan-runner/bin/gh')
            count = int(child_env.get('GIT_CONFIG_COUNT', '0'))
            for index, value in enumerate(('', helper), start=count):
                child_env[f'GIT_CONFIG_KEY_{index}'] = 'credential.https://github.com.helper'
                child_env[f'GIT_CONFIG_VALUE_{index}'] = value
            child_env['GIT_CONFIG_COUNT'] = str(count + 2)
        return subprocess.run(argv, input=note + '\n' + sys.stdin.read(), text=True,
                              cwd=cwd, env=child_env).returncode
    if engine == 'pi':
        sessions = str(Path(os.environ['PI_CODING_AGENT_DIR']) / 'sessions' / os.environ['ITSAPLAN_PROJECT_KEY'].lower())
        # The user's pi wrapper refreshes models/keys from global daily config.
        # Invoke the installed CLI directly to keep the Runner profile isolated.
        return subprocess.run(['/usr/local/bin/pi', '--session-dir', sessions,
                               '--append-system-prompt', note, '--approve', *args], cwd=cwd).returncode
    context = os.environ.get('ITSAPLAN_SYSTEM_PROMPT', '')
    prompt = '\n\n'.join((note, context, sys.stdin.read()))
    if engine == 'grok':
        profile = Path(os.environ['GROK_HOME'])
        credentials = json.loads((profile / 'credentials.json').read_text())
        child_env = dict(os.environ)
        child_env['XAI_API_KEY'] = credentials['XAI_API_KEY']
        child_env['GROK_CODE_XAI_API_KEY'] = credentials['XAI_API_KEY']
        child_env.pop('GROK_CONFIG', None)
        child_env.pop('GROK_CONFIG_PATH', None)
        with tempfile.NamedTemporaryFile(mode='w', prefix='itsaplan-grok-', suffix='.txt') as handle:
            handle.write(prompt)
            handle.flush()
            return subprocess.run(['/opt/itsaplan-runner/bin/grok', '--prompt-file', handle.name,
                                   '--always-approve', '--output-format', 'plain',
                                   *args], cwd=cwd, env=child_env).returncode
    if engine == 'cursor':
        # Cursor CLI does not discover these materialized local plugins solely
        # from CURSOR_CONFIG_DIR. Load the shared profile's plugins explicitly.
        local = Path(os.environ['CURSOR_CONFIG_DIR']) / 'plugins' / 'local'
        plugin_args = []
        if local.is_dir():
            for plugin in sorted(local.iterdir()):
                if (plugin / '.cursor-plugin' / 'plugin.json').is_file():
                    plugin_args.extend(['--plugin-dir', str(plugin)])
        return subprocess.run(['/opt/itsaplan-runner/bin/cursor-agent', '--print', '--output-format', 'text',
                               '--trust', '--force', '--approve-mcps', '--sandbox', 'disabled',
                               *plugin_args, *args, prompt], cwd=cwd,
                              env=os.environ).returncode
    raise RuntimeError('Unsupported engine')


def main(engine, args):
    try:
        run_id = os.environ.get('ITSAPLAN_RUN_ID')
        if run_id:
            config = read_config()
            project = os.environ['ITSAPLAN_PROJECT_KEY']
            invocation = Invocation(config, project, run_id)
            with project_gate(config, project):
                with lease(config, project, run_id):
                    exit_code = run(engine, args)
                    requested = invocation.child_ended(exit_code)
            if requested:
                os.chdir('/opt/itsaplan-runner')
                try:
                    invocation.finalize(requested)
                except Exception as error:
                    print(f'Agent-requested finalize retained workspaces: {error}', file=sys.stderr)
                    try:
                        invocation.report(requested, [{'outcome': 'retained:finalize-error:' + str(error)}])
                    except Exception:
                        print('Finalize report unavailable; inspect protected record.', file=sys.stderr)
            return exit_code
        return run(engine, args)
    except Exception as error:
        print(f'itsaplan workspace adapter: {error}', file=sys.stderr)
        return 1
