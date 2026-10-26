import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workspace_adapter import allocate


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def git(self, path, *args):
        return subprocess.check_output(
            ['/usr/bin/git', '-C', str(path), *args], text=True,
            stderr=subprocess.PIPE).strip()

    def repository(self, name):
        source = self.base / 'sources' / name
        source.mkdir(parents=True)
        self.git(source, 'init', '--quiet')
        self.git(source, 'config', 'user.name', 'fixture-user')
        self.git(source, 'config', 'user.email', 'fixture@example.invalid')
        self.git(source, 'remote', 'add', 'origin', str(source))
        self.commit(source, 'initial')
        return source

    def commit(self, source, value):
        (source / 'content.txt').write_text(value)
        self.git(source, 'add', 'content.txt')
        self.git(source, 'commit', '--quiet', '-m', value)
        return self.git(source, 'rev-parse', 'HEAD')

    def mapping(self, name, source, **extra):
        return dict(name=name, path=str(source),
                    origin=self.git(source, 'remote', 'get-url', 'origin'), **extra)

    def nested_project(self):
        parent = self.repository('parent')
        upstream = self.repository('upstream-child')
        pinned = self.git(upstream, 'rev-parse', 'HEAD')
        self.git(parent, '-c', 'protocol.file.allow=always', 'submodule',
                 'add', '--quiet', str(upstream), 'orion')
        self.git(parent, 'commit', '--quiet', '-am', 'Add child gitlink')
        child = parent / 'orion'
        self.git(child, 'config', 'user.name', 'fixture-user')
        self.git(child, 'config', 'user.email', 'fixture@example.invalid')
        newer = self.commit(upstream, 'newer child revision')
        self.git(child, 'fetch', '--quiet', 'origin')
        self.git(child, 'checkout', '--quiet', newer)
        third = self.repository('third')
        project = {'worktreeRoot': str(self.base / 'tasks'), 'repositories': [
            self.mapping('parent', parent),
            self.mapping('parent/orion', child, parent='parent', submodule='orion'),
            self.mapping('third', third),
        ]}
        return project, parent, child, pinned, newer

    def test_nested_submodule_uses_parent_gitlink_and_preserves_retry_changes(self):
        project, parent, child, pinned, newer = self.nested_project()
        source_status = self.git(parent, 'status', '--porcelain')
        root, records = allocate('TST', project, '61')
        checkout = root / 'parent/orion'
        self.assertTrue((child / '.git').is_file())
        self.assertTrue((checkout / '.git').is_file())
        self.assertEqual(self.git(checkout, 'rev-parse', 'HEAD'), pinned)
        self.assertNotEqual(pinned, newer)
        self.assertEqual(records[1]['baseCommit'], pinned)
        self.assertEqual(len(records), 3)
        (checkout / 'content.txt').write_text('task changes')
        (root / 'parent' / 'untracked.txt').write_text('preserve me')
        retry_root, retry_records = allocate('TST', project, '61')
        self.assertEqual(retry_root, root)
        self.assertEqual(retry_records, records)
        self.assertEqual((checkout / 'content.txt').read_text(), 'task changes')
        self.assertEqual((root / 'parent' / 'untracked.txt').read_text(), 'preserve me')
        self.assertEqual(self.git(child, 'rev-parse', 'HEAD'), newer)
        self.assertEqual(self.git(parent, 'status', '--porcelain'), source_status)

    def test_partial_parent_allocation_resumes_without_replacing_parent(self):
        project, parent, child, pinned, newer = self.nested_project()
        partial = dict(project, repositories=project['repositories'][:1])
        root, _ = allocate('TST', partial, '62')
        self.assertTrue((root / 'parent/orion').is_dir())
        self.assertFalse((root / 'parent/orion/.git').exists())
        (root / 'parent' / 'untracked.txt').write_text('partial task changes')
        _, records = allocate('TST', project, '62')
        self.assertEqual(len(records), 3)
        self.assertEqual(self.git(root / 'parent/orion', 'rev-parse', 'HEAD'), pinned)
        self.assertEqual((root / 'parent' / 'untracked.txt').read_text(), 'partial task changes')

    def test_three_independent_repositories_keep_original_behavior(self):
        sources = [self.repository(name) for name in ('one', 'two', 'three')]
        project = {'worktreeRoot': str(self.base / 'tasks'), 'repositories': [
            self.mapping(source.name, source) for source in sources]}
        root, records = allocate('DGV', project, '65')
        self.assertEqual(len(records), 3)
        for source, record in zip(sources, records):
            self.assertEqual(record['baseCommit'], self.git(source, 'rev-parse', 'HEAD'))
            self.assertEqual(record['branch'], 'fixture-user/dgv-65')
            self.assertEqual(self.git(source, 'status', '--porcelain'), '')
        (root / 'two/content.txt').write_text('keep task edits')
        self.assertEqual(allocate('DGV', project, '65')[1], records)
        self.assertEqual((root / 'two/content.txt').read_text(), 'keep task edits')


if __name__ == '__main__':
    unittest.main()
