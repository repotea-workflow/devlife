#!/usr/bin/env python3
"""Copy stopped Runner state into isolated persistence; never print credentials."""
import json
import os
from pathlib import Path
import shutil


def prepare():
    source = Path('/root/.config/itsaplan-runner')
    base = Path('/data/itsaplan/runner')
    state = base / 'state'
    if state.exists():
        raise RuntimeError('Migration destination exists; preserve it and inspect before proceeding')
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(source, state, symlinks=True)
    config = json.loads((state / 'runner.json').read_text())
    for agent in config['agents']:
        env = agent.setdefault('env', {})
        profile = Path(env['ITSAPLAN_PROFILE_DIR'])
        engine = profile.name
        if engine not in ('codex', 'pi', 'grok', 'cursor') or not profile.is_dir():
            raise RuntimeError('Expected one existing independent profile per engine')
        home = base / 'homes' / engine
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env['HOME'] = str(home)
    (state / 'runner.json').write_text(json.dumps(config, indent=2) + '\n')
    (state / 'runner.json').chmod(0o600)
    state.chmod(0o700)
    cli = base / 'home' / '.config' / 'aicli' / 'itsaplan'
    cli.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree('/root/.config/aicli/itsaplan', cli, symlinks=True)
    cli.chmod(0o700)
    os.chmod(base, 0o700)
    print('Prepared isolated Runner state; original configuration and profiles preserved')


if __name__ == '__main__':
    prepare()
