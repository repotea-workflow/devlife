"""Enforce Cursor's workspace boundary around all of its native tools."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def cursor(argv, cwd, repos, profile, git):
    profile = Path(profile)
    # Cursor atomically rewrites cli-config.json even with a model override.
    # Read the shared profile into an invocation-local config; persistent
    # credentials, settings and plugins remain read-only inside the sandbox.
    with tempfile.TemporaryDirectory(prefix='itsaplan-cursor-profile-') as folder:
        runtime = Path(folder)
        local = runtime / 'cursor'
        local.mkdir(mode=0o700)
        for source in profile.iterdir():
            if source.is_file() and not source.is_symlink():
                shutil.copy2(source, local / source.name)
                (local / source.name).chmod(0o600)
        mounts = [str(cwd), os.environ['HOME']]
        for name in ('chats', 'cache'):
            target = profile / name
            target.mkdir(mode=0o700, exist_ok=True)
            (local / name).symlink_to(target)
            mounts.append(str(target))
        if (profile / 'plugins').is_dir():
            (local / 'plugins').symlink_to(profile / 'plugins')
        command = ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid',
                   '--die-with-parent', '--ro-bind', '/', '/', '--dev', '/dev',
                   '--proc', '/proc', '--tmpfs', '/tmp',
                   '--bind', str(local), str(local)]
        # Expose only this invocation's nonce-bound finalize channel. A shared
        # writable /tmp would let concurrent commands alter each other's requests.
        request = os.environ.get('ITSAPLAN_FINALIZE_REQUEST_DIR')
        if request:
            channel = Path(request)
            if not channel.is_absolute() or channel.resolve() != channel or not channel.is_dir():
                raise RuntimeError('Invalid invocation finalize directory')
            command.extend(['--bind', str(channel), str(channel)])
        for path in mounts:
            command.extend(['--bind', path, path])
        for repo in repos:
            common = git(repo['source'], 'rev-parse', '--absolute-git-dir')
            command.extend(['--bind', common, common])
            for path in (Path(common) / 'config', Path(common) / 'hooks',
                         Path(repo['path']) / '.git'):
                if path.exists():
                    command.extend(['--ro-bind', str(path), str(path)])
        command.extend(['--chdir', str(cwd), *argv])
        # The API CLI initializes its context directory. Keep that writable
        # inside this invocation instead of under the read-only shared profile.
        env = dict(os.environ, CURSOR_CONFIG_DIR=str(local),
                   XDG_CONFIG_HOME=str(runtime),
                   XDG_CACHE_HOME=str(runtime / 'runtime-cache'))
        return subprocess.run(command, cwd=cwd, env=env).returncode
