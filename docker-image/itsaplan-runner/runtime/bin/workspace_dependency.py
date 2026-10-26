#!/usr/bin/python3
"""Register an independent review dependency before delegating it."""
import argparse
import fcntl
from pathlib import Path

from workspace_lifecycle import CONFIG, Platform, atomic_json, read_config, validate_config


def add_dependency(project, main, dependency):
    config = read_config()
    lock = Path(config['stateRoot']) / 'finalize.lock'
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        config = read_config()
        platform = Platform(config)
        root = platform.issue(main, fresh=True)
        child = platform.issue(dependency, fresh=True)
        project_data = platform.project(project)
        if any(obj['projectId'] != project_data['project']['id'] for obj in (root, child)):
            raise RuntimeError('Dependency belongs to another project')
        if root.get('parentId') or child.get('parentId') or main == dependency:
            raise RuntimeError('Independent dependency requires two distinct root issues')
        states = {c['id']: c['stateType'] for c in project_data['columns']}
        if states[root['columnId']] in ('completed', 'canceled'):
            raise RuntimeError('Register dependency while the main task is still open')
        mapping = config.setdefault('issueDependencies', {})
        children = mapping.setdefault(str(main), [])
        if dependency not in children:
            children.append(dependency)
        validate_config(config)
        config.pop('_configPath', None)
        atomic_json(CONFIG, config)
    return {'project': project, 'mainIssueId': main, 'dependencyIssueId': dependency}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('main', type=int)
    parser.add_argument('dependency', type=int)
    args = parser.parse_args()
    print(add_dependency(args.project, args.main, args.dependency))
