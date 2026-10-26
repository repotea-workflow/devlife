#!/usr/bin/python3
"""Restore and verify a lifecycle snapshot into a new private directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

from workspace_archive import verify_archive
from workspace_lifecycle import git, REGISTRY


def recover(snapshot, destination):
    snapshot, destination = Path(snapshot), Path(destination)
    manifest = verify_archive(snapshot)
    if destination.exists():
        raise RuntimeError('Recovery requires a new destination; existing files are preserved')
    destination.mkdir(mode=0o700, parents=True)
    root = Path(manifest['ownership']['root'])
    sources = {r['path']: r for r in manifest['ownership']['repositories']}
    project = json.loads(REGISTRY.read_text()).get(manifest['ownership']['project'], {}) if REGISTRY.exists() else {}
    aliases = project.get('sourceAliases', {})
    configured = project.get('repositories', [])
    remappings = []
    for repo in sorted(manifest['repositories'], key=lambda r: len(Path(r['path']).parts)):
        checkout = destination / Path(repo['path']).relative_to(root)
        saved = snapshot / repo['archiveDirectory']
        if repo['ordinarySubmodule']:
            source = saved / 'commits.bundle'
        else:
            owned = sources[repo['path']]
            original = owned['source']
            source = Path(aliases.get(original, original))
            if str(source) != original:
                matches = [r for r in configured if r['name'] == owned['name']]
                if len(matches) != 1:
                    raise RuntimeError('Recovery alias requires one matching named repository')
                target = Path(matches[0]['path'])
                if (source != target or not source.is_absolute() or '..' in source.parts
                        or source.is_symlink() or target.is_symlink()
                        or source.resolve() != source or target.resolve() != target
                        or git(source, 'remote', 'get-url', 'origin') != matches[0]['origin']):
                    raise RuntimeError('Recovery source alias must match the named canonical repository and origin')
                remappings.append({'originalSource': original, 'currentSource': str(source)})
        subprocess.run(['/usr/bin/git', 'clone', '--no-checkout', '--no-hardlinks',
                        str(source), str(checkout)], capture_output=True, check=True)
        bundle = saved / 'commits.bundle'
        if bundle.exists() and not repo['ordinarySubmodule']:
            git(checkout, 'fetch', str(bundle), 'HEAD')
        git(checkout, 'checkout', '--detach', repo['headCommit'])
        if (saved / 'index.patch').stat().st_size:
            git(checkout, 'apply', '--index', str(saved / 'index.patch'))
        if (saved / 'working.patch').stat().st_size:
            git(checkout, 'apply', str(saved / 'working.patch'))
        with tarfile.open(saved / 'untracked-and-ignored.tar') as archive:
            archive.extractall(checkout, filter='data')
        for file in repo['files']:
            restored = checkout / file['path']
            value = os.fsencode(os.readlink(restored)) if file['kind'] == 'symlink' else restored.read_bytes()
            if hashlib.sha256(value).hexdigest() != file['sha256']:
                raise RuntimeError('Recovered repository artifact checksum mismatch')
        for cached, filename in ((True, 'index.patch'), (False, 'working.patch')):
            args = ['diff'] + (['--cached'] if cached else [])
            actual = git(checkout, *args, '--binary', '--no-ext-diff',
                         '--ignore-submodules=dirty', binary=True)
            if actual != (saved / filename).read_bytes():
                raise RuntimeError('Recovered index or working diff mismatch')
    with tarfile.open(snapshot / 'task-artifacts.tar') as archive:
        archive.extractall(destination, filter='data')
    for file in manifest['extra']:
        restored = destination / file['path']
        value = os.fsencode(os.readlink(restored)) if file['kind'] == 'symlink' else restored.read_bytes()
        if hashlib.sha256(value).hexdigest() != file['sha256']:
            raise RuntimeError('Recovered task artifact checksum mismatch')
    return {'snapshot': str(snapshot), 'recovery': str(destination),
            'fingerprint': manifest['fingerprint'], 'repositoriesVerified': len(manifest['repositories']), 'sourceRemappings': remappings}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot')
    parser.add_argument('destination')
    args = parser.parse_args()
    print(recover(args.snapshot, args.destination))
