"""Recoverable, verified snapshots before Git-native task workspace removal."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

from workspace_lifecycle import atomic_json, git

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
SECRET_NAME = re.compile(r'(^|/)(\.env($|\.(?!example$|sample$|template$))|credentials\.json$|auth\.json$|[^/]+\.(pem|key)$)', re.I)
SECRET_CONTENT = re.compile(rb"""-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}|\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[=:]\s*(["'])([A-Za-z0-9+/_.-]{20,})\1""", re.I)

PLAIN_SECRET = re.compile(rb'(?m)^\s*(?:export\s+)?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[=:]\s*([A-Za-z0-9+/_.-]{20,})\s*(?:#[^\n]*)?$', re.I)



def digest(value):
    return hashlib.sha256(value).hexdigest()


def check_secret(name, data):
    plain = any(not m.group(1).startswith((b'process.env.', b'os.environ', b'import.meta.env.'))
                for m in PLAIN_SECRET.finditer(data))
    if SECRET_NAME.search(name) or SECRET_CONTENT.search(data) or plain:
        raise RuntimeError('Potential sensitive material found; preserve workspace instead of archiving it')


def file_inventory(root, tracked=(), excluded=(), verified_git=False):
    root = Path(root)
    tracked = set(tracked)
    excluded = {Path(p).absolute() for p in excluded}
    files, omitted = [], []
    def traversal_error(error):
        raise error
    total = 0
    for current, dirs, names in os.walk(root, followlinks=False, onerror=traversal_error):
        current = Path(current)
        for name in list(dirs):
            child = current / name
            relative = str(child.relative_to(root))
            if name == '.git' and (current != root or not verified_git):
                raise RuntimeError('Unowned nested Git repository; preserve workspace')
            if name == '.git' or child.absolute() in excluded:
                dirs.remove(name)
            elif child.is_symlink():
                names.append(name)
                dirs.remove(name)
        for name in names:
            p = current / name
            relative = str(p.relative_to(root))
            if name == '.git' and (current != root or not verified_git):
                raise RuntimeError('Unowned nested Git checkout; preserve workspace')
            if name == '.git' or relative in tracked:
                continue
            if p.is_symlink():
                data = os.fsencode(os.readlink(p))
                kind = 'symlink'
            elif p.is_file():
                if p.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Artifact exceeds safety budget; preserve workspace")
                data = p.read_bytes()
                kind = 'file'
            else:
                raise RuntimeError('Non-regular task artifact; preserve workspace')
            total += len(data)
            if total > MAX_ARCHIVE_BYTES:
                raise RuntimeError("Artifact archive exceeds safety budget; preserve workspace")
            check_secret(relative, data)
            files.append({'path': relative, 'sha256': digest(data), 'kind': kind, 'mode': p.lstat().st_mode & 0o777})
    return sorted(files, key=lambda f: f['path']), sorted(omitted)


def scan_history(path, head, base=None):
    revisions = [head] + (['^' + base] if base else [])
    # Inspect every changed name, including secrets deleted in later commits.
    names = git(path, 'log', '--format=', '--name-only', '-z', *revisions, binary=True)
    for name in names.split(b'\0'):
        if name.strip():
            check_secret(os.fsdecode(name.strip()), b'')
    objects = git(path, 'rev-list', '--objects', *revisions, binary=True).splitlines()
    identities = [entry.split(b' ', 1)[0] for entry in objects]
    request = b'\n'.join(identities) + b'\n'
    result = subprocess.run(['/usr/bin/git', '-C', str(path), 'cat-file', '--batch-check'],
                            input=request, capture_output=True, check=True, timeout=300)
    blobs = []
    total = 0
    for line in result.stdout.splitlines():
        sha, kind, size = line.split()
        if kind == b'blob':
            total += int(size)
            blobs.append(sha)
    if total > MAX_ARCHIVE_BYTES:
        raise RuntimeError('Bundle history exceeds inspection budget; preserve workspace')
    if not blobs:
        return
    result = subprocess.run(['/usr/bin/git', '-C', str(path), 'cat-file', '--batch'],
                            input=b'\n'.join(blobs) + b'\n', capture_output=True, check=True, timeout=300)
    offset = 0
    for sha in blobs:
        end = result.stdout.index(b'\n', offset)
        actual, kind, size = result.stdout[offset:end].split()
        if actual != sha or kind != b'blob':
            raise RuntimeError('Bundle object inspection mismatch')
        offset = end + 1
        size = int(size)
        check_secret('bundle-blob', result.stdout[offset:offset + size])
        offset += size + 1


def repository_snapshots(record):
    repos = []
    pending = [(Path(r['path']), r.get('baseCommit'), r['name']) for r in record['repositories'] if Path(r['path']).exists()]
    seen = set()
    while pending:
        path, base, name = pending.pop(0)
        if path in seen:
            continue
        seen.add(path)
        flags = git(path, 'ls-files', '-v', '-z', binary=True).split(b'\0')
        if any(f and (f[:1].islower() or f[:1] == b'S') for f in flags):
            raise RuntimeError('Hidden tracked-file flags; preserve workspace')
        # Include initialized ordinary submodules; separately linked children are
        # already manifest-owned and get their own source/branch recovery data.
        entries = git(path, 'ls-files', '--stage', '-z', binary=True).split(b'\0')
        children = []
        for entry in entries:
            if entry.startswith(b'160000 '):
                metadata, rel = entry.split(b'\t', 1)
                child = path / os.fsdecode(rel)
                if child.is_symlink() or path.resolve() not in child.resolve().parents:
                    raise RuntimeError('Submodule escapes owned checkout')
                if (child / '.git').exists():
                    linked = any(Path(r['path']) == child for r in record['repositories'])
                    child_git = Path(git(child, 'rev-parse', '--absolute-git-dir')).resolve()
                    parent_git = Path(git(path, 'rev-parse', '--absolute-git-dir')).resolve()
                    if not linked and (parent_git / 'modules') not in child_git.parents:
                        raise RuntimeError('Unowned ordinary submodule metadata')
                    children.append(child)
                    pending.append((child, metadata.split()[1].decode(), name + '--submodule-' + digest(rel)[:12]))
        head = git(path, 'rev-parse', 'HEAD')
        index = git(path, 'diff', '--cached', '--binary', '--no-ext-diff', '--ignore-submodules=dirty', binary=True)
        work = git(path, 'diff', '--binary', '--no-ext-diff', '--ignore-submodules=dirty', binary=True)
        committed = git(path, 'diff', base or head, head, '--binary', '--no-ext-diff', '--ignore-submodules=dirty', binary=True)
        changed_names = git(path, 'diff', '--name-only', base or head, binary=True).decode(errors='surrogateescape').splitlines()
        for name_changed in changed_names:
            check_secret(name_changed, b'')
        check_secret('index.patch', index)
        check_secret('working.patch', work)
        check_secret('committed.patch', committed)
        tracked = [os.fsdecode(f) for f in git(path, 'ls-files', '-z', binary=True).split(b'\0') if f]
        nested = [Path(r['path']) for r in record['repositories'] if path in Path(r['path']).parents]
        files, omitted = file_inventory(path, tracked, children + nested, verified_git=True)
        ordinary = path not in {Path(r['path']) for r in record['repositories']}
        bundle = ordinary or head != (base or head)
        if bundle:
            scan_history(path, head, None if ordinary else base)
        # Check actual binary bytes: a binary patch does not expose secrets.
        changed = {os.fsdecode(f) for mode in ([], ['--cached'])
                   for f in git(path, 'diff', *mode, '--name-only', '-z', binary=True).split(b'\0') if f}
        for relative in changed:
            target = path / relative
            if target.is_file() and not target.is_symlink():
                check_secret(relative, target.read_bytes())
        for entry in entries:
            if entry and not entry.startswith(b'160000 '):
                metadata, relative = entry.split(b'\t', 1)
                if os.fsdecode(relative) not in changed:
                    continue
                check_secret(os.fsdecode(relative), git(path, 'cat-file', 'blob', metadata.split()[1].decode(), binary=True))
        repos.append({'ordinarySubmodule': ordinary, 'bundleRequired': bundle, 'name': name, 'path': str(path), 'baseCommit': base or head, 'headCommit': head,
                      'branch': git(path, 'branch', '--show-current'), 'files': files,
                      'omittedReproducible': omitted, 'indexPatch': index, 'workingPatch': work,
                      'committedPatch': committed})
    return repos


def inventory(record):
    repos = repository_snapshots(record)
    roots = [r['path'] for r in record['repositories']]
    if str(record['root']) in roots:
        extra, omitted = [], []
    else:
        extra, omitted = file_inventory(record['root'], excluded=roots)
    value = {'repositories': repos, 'extra': extra, 'omittedReproducible': omitted}
    compact = dict(value, repositories=[dict(r, indexPatch=digest(r['indexPatch']),
                                              workingPatch=digest(r['workingPatch']),
                                              committedPatch=digest(r['committedPatch'])) for r in repos])
    return value, digest(json.dumps(compact, sort_keys=True).encode())


def write_bytes(path, data):
    with Path(path).open('wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    Path(path).chmod(0o600)


def make_tar(path, root, files):
    with tarfile.open(path, 'w') as tar:
        for file in files:
            tar.add(Path(root) / file['path'], arcname=file['path'], recursive=False)
    Path(path).chmod(0o600)
    # Verify the actual stored bytes and paths, not just successful tar creation.
    expected = {f['path']: f for f in files}
    with tarfile.open(path) as tar:
        members = tar.getmembers()
        if {m.name for m in members} != set(expected):
            raise RuntimeError('Artifact archive member mismatch')
        for m in members:
            f = expected[m.name]
            data = os.fsencode(m.linkname) if m.issym() else tar.extractfile(m).read()
            if digest(data) != f['sha256']:
                raise RuntimeError('Artifact archive checksum mismatch')
    with Path(path).open('rb') as f:
        os.fsync(f.fileno())


def archive(record, config, snapshot, fingerprint):
    destination = Path(config['archiveRoot']) / record['project'] / ('issue-' + str(record['issueId'])) / ('run-' + record['runId']) / fingerprint
    if (destination / 'manifest.json').exists():
        verify_archive(destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=destination.parent))
    manifest = {'version': 1, 'ownership': record, 'fingerprint': fingerprint, 'repositories': [],
                'extra': snapshot['extra'], 'omittedReproducible': snapshot['omittedReproducible'], 'checksums': {}}
    for i, repo in enumerate(snapshot['repositories']):
        subdir = temporary / ('repo-' + str(i))
        subdir.mkdir(mode=0o700)
        metadata = {k: v for k, v in repo.items() if not k.endswith('Patch')}
        metadata['archiveDirectory'] = subdir.name
        for key, filename in [('indexPatch', 'index.patch'), ('workingPatch', 'working.patch'), ('committedPatch', 'committed.patch')]:
            write_bytes(subdir / filename, repo[key])
        make_tar(subdir / 'untracked-and-ignored.tar', repo['path'], repo['files'])
        if repo['bundleRequired']:
            # Preserve committed/unmerged history without pushing or deleting refs.
            git(repo['path'], 'bundle', 'create', str(subdir / 'commits.bundle'), 'HEAD',
                *([] if repo['ordinarySubmodule'] else ['^' + repo['baseCommit']]))
            git(repo['path'], 'bundle', 'verify', str(subdir / 'commits.bundle'))
            (subdir / 'commits.bundle').chmod(0o600)
        manifest['repositories'].append(metadata)
    make_tar(temporary / 'task-artifacts.tar', record['root'], snapshot['extra'])
    for p in temporary.rglob('*'):
        if p.is_file():
            manifest['checksums'][str(p.relative_to(temporary))] = digest(p.read_bytes())
            with p.open('rb') as f:
                os.fsync(f.fileno())
    atomic_json(temporary / 'manifest.json', manifest)
    verify_archive(temporary)
    for current, _, _ in os.walk(temporary):
        fd = os.open(current, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    os.rename(temporary, destination)
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    atomic_json(destination.parent / 'latest.json', {'snapshot': str(destination), 'fingerprint': fingerprint})
    return destination


def verify_archive(path):
    path = Path(path)
    manifest = json.loads((path / 'manifest.json').read_text())
    for relative, expected in manifest['checksums'].items():
        p = path / relative
        if p.is_symlink() or digest(p.read_bytes()) != expected:
            raise RuntimeError('Durable archive checksum mismatch')
    return manifest
