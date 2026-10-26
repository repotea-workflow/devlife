"""Native Git integration tests; fixture sources/archives remain for inspection."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from workspace_lifecycle import atomic_json, git, lease, project_gate, record_path, register, registered
from workspace_archive import verify_archive, check_secret
import workspace_cleanup as cleanup


class Platform:
    run_cache = None

    def __init__(self):
        self.issues = {1: {'id': 1, 'projectId': 1, 'columnId': 4, 'parentId': None,
                           'statusSince': '2020-01-01T00:00:00Z', 'subtasks': []}}
        self.queue = [{'id': 1, 'issueId': 1, 'status': 'success', '_agentId': 1}]

    def issue(self, issue, fresh=False):
        return copy.deepcopy(self.issues[issue])

    def root_issue(self, issue):
        return self.issues[issue].get('parentId') or issue

    def project(self, project):
        return {'project': {'id': 1}, 'columns': [{'id': 2, 'stateType': 'unstarted'},
                            {'id': 3, 'stateType': 'started'}, {'id': 4, 'stateType': 'completed'}]}

    def runs(self):
        return self.queue


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='itsaplan-cleanup-native-'))
        self.config = {'stateRoot': str(self.base / 'state'), 'archiveRoot': str(self.base / 'archives'),
                       'enabled': True, 'quietSeconds': 0, 'agentIds': [1], 'teamId': 1, 'issueDependencies': {}}
        self.registry = {'TST': {'worktreeRoot': str(self.base / 'worktrees'), 'repositories': []}}
        self.platform = Platform()
        self.root = self.base / 'worktrees/run-1'
        self.root.mkdir(parents=True)
        self.repos = []

    def source(self, name):
        source = self.base / 'sources' / name
        source.mkdir(parents=True)
        git(source, 'init', '--initial-branch=main')
        git(source, 'config', 'user.name', 'Fixture')
        git(source, 'config', 'user.email', 'fixture@localhost')
        git(source, 'config', 'commit.gpgsign', 'false')
        git(source, 'remote', 'add', 'origin', str(self.base / ('origin-' + name)))
        (source / 'code.txt').write_text('base\n')
        (source / '.gitignore').write_text('node_modules/\n.cache/\nartifacts/\n')
        git(source, 'add', 'code.txt', '.gitignore')
        git(source, 'commit', '-m', 'Fixture baseline')
        self.registry['TST']['repositories'].append({'name': name, 'path': str(source),
                                                    'origin': git(source, 'remote', 'get-url', 'origin')})
        return source

    def worktree(self, name, source=None, path=None):
        source = source or self.source(name)
        path = path or self.root / name
        git(source, 'worktree', 'add', '-b', 'fixture/tst-1', str(path), 'HEAD')
        repo = {'name': name, 'source': str(source), 'path': str(path), 'branch': 'fixture/tst-1',
                'baseCommit': git(path, 'rev-parse', 'HEAD')}
        self.repos.append(repo)
        return path

    def owned(self):
        atomic_json(self.root / 'workspace.json', {'project': 'TST', 'runId': '1',
                                                  'repositories': self.repos})
        return register(self.config, self.registry, 'TST', '1', 1, self.root, self.repos)

    def clean(self, record):
        return cleanup.clean_record(record, self.config, self.registry, self.platform)

    def test_dirty_multiple_repos_recovery_and_idempotence(self):
        first = self.worktree('first')
        second = self.worktree('second')
        (first / 'code.txt').write_text('staged\n')
        git(first, 'add', 'code.txt')
        (first / 'code.txt').write_text('final unstaged\n')
        (first / 'extra.txt').write_text('untracked proof\n')
        (first / 'artifacts').mkdir()
        (first / 'artifacts/checks.txt').write_text('checks passed\n')
        (first / 'node_modules').mkdir()
        (first / 'node_modules/generated').write_text('reproducible\n')
        (self.root / '.cache').mkdir()
        (self.root / '.cache/generated').write_text('reproducible\n')
        (self.root / 'review.txt').write_text('independent review passed\n')
        record = self.owned()
        self.assertEqual(self.clean(record), 'cleaned')
        self.assertFalse(self.root.exists())
        self.assertTrue(all(not registered(r['source'], r['path']) for r in self.repos))
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        archive = Path(saved['archives'][0])
        manifest = verify_archive(archive)
        restored = self.base / 'restored'
        git(record['repositories'][0]['source'], 'worktree', 'add', '--detach', str(restored), manifest['repositories'][0]['headCommit'])
        repo_archive = archive / manifest['repositories'][0]['archiveDirectory']
        git(restored, 'apply', '--index', str(repo_archive / 'index.patch'))
        git(restored, 'apply', str(repo_archive / 'working.patch'))
        with tarfile.open(repo_archive / 'untracked-and-ignored.tar') as t:
            t.extractall(restored, filter='data')
        self.assertEqual((restored / 'code.txt').read_text(), 'final unstaged\n')
        self.assertEqual((restored / 'extra.txt').read_text(), 'untracked proof\n')
        self.assertEqual((restored / 'artifacts/checks.txt').read_text(), 'checks passed\n')
        with tarfile.open(archive / 'task-artifacts.tar') as t:
            self.assertEqual(t.extractfile('review.txt').read(), b'independent review passed\n')
        self.assertEqual(self.clean(record), 'already-cleaned')
        self.assertTrue(git(record['repositories'][0]['source'], 'show-ref', 'refs/heads/fixture/tst-1'))

    def test_cache_evidence_is_recovered(self):
        wt = self.worktree('repo'); record = self.owned()
        (wt / '.cache').mkdir()
        (wt / '.cache/checks.txt').write_text('irreplaceable evidence')
        self.assertEqual(self.clean(record), 'cleaned')
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        manifest = verify_archive(saved['archives'][0])
        tar = Path(saved['archives'][0]) / manifest['repositories'][0]['archiveDirectory'] / 'untracked-and-ignored.tar'
        with tarfile.open(tar) as t:
            self.assertEqual(t.extractfile('.cache/checks.txt').read(), b'irreplaceable evidence')

    def test_removed_committed_secret_is_retained(self):
        wt = self.worktree('repo'); record = self.owned()
        (wt / 'secret.txt').write_text('sk-' + 'a' * 30)
        git(wt, 'add', 'secret.txt'); git(wt, 'commit', '-m', 'Fixture secret')
        git(wt, 'rm', 'secret.txt'); git(wt, 'commit', '-m', 'Fixture removal')
        with self.assertRaisesRegex(RuntimeError, 'sensitive'):
            self.clean(record)
        self.assertTrue(wt.exists())
        self.assertFalse((self.base / 'archives').exists())

    def test_unowned_container_git_is_retained(self):
        self.worktree('repo'); record = self.owned()
        git(self.root, 'init')
        with self.assertRaisesRegex(RuntimeError, 'Unowned nested'):
            self.clean(record)
        self.assertTrue((self.root / '.git').exists())

    def test_hidden_tracked_modification_is_retained(self):
        wt = self.worktree('repo'); record = self.owned()
        git(wt, 'update-index', '--assume-unchanged', 'code.txt')
        (wt / 'code.txt').write_text('hidden change')
        with self.assertRaisesRegex(RuntimeError, 'Hidden tracked'):
            self.clean(record)
        self.assertTrue(wt.exists())

    def test_separate_review_dependency_blocks(self):
        self.worktree('repo'); record = self.owned()
        self.config['issueDependencies'] = {'1': [2]}
        self.platform.issues[2] = dict(self.platform.issues[1], id=2, columnId=3)
        self.assertEqual(self.clean(record), 'retained:review-or-subtask-not-Done')
        self.platform.issues[2]['columnId'] = 4
        self.platform.queue.append({'id': 2, 'issueId': 2, 'status': 'success', '_agentId': 1})
        self.assertEqual(self.clean(record), 'cleaned')

    def test_submodule_objects_restore_after_removal(self):
        _, child = self.nested(False); record = self.owned()
        self.assertEqual(self.clean(record), 'cleaned')
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        manifest = verify_archive(saved['archives'][0])
        sub = next(r for r in manifest['repositories'] if r['path'] == str(child))
        folder = Path(saved['archives'][0]) / sub['archiveDirectory']
        restore = self.base / 'restored-submodule'
        git(self.base, 'clone', str(folder / 'commits.bundle'), str(restore))
        git(restore, 'checkout', '--detach', sub['headCommit'])
        git(restore, 'apply', str(folder / 'working.patch'))
        self.assertEqual((restore / 'code.txt').read_text(), 'submodule uncommitted\n')


    def test_new_run_gate_blocks_cleanup_before_registration(self):
        self.worktree('repo'); record = self.owned()
        with project_gate(self.config, 'TST'):
            with lease(self.config, 'TST', '99'):
                self.assertEqual(self.clean(record), 'retained:project-execution-active')
        self.assertEqual(self.clean(record), 'cleaned')


    def test_plain_literal_secret_and_expression_reference(self):
        with self.assertRaisesRegex(RuntimeError, 'sensitive'):
            check_secret('config.txt', b'api_key=abcdefghijklmnopqrstuvwx\n')
        check_secret('script.js', b'API_KEY = process.env.INDEXER_API_KEY\n')
        wt = self.worktree('repo'); record = self.owned()
        (wt / 'config.txt').write_text('api_key=abcdefghijklmnopqrstuvwx\n')
        with self.assertRaisesRegex(RuntimeError, 'sensitive'):
            self.clean(record)
        self.assertTrue(wt.exists())


    def test_new_process_cannot_enter_during_retirement(self):
        self.worktree('repo'); record = self.owned()
        original = cleanup.archive
        child = None
        marker = self.base / 'new-run-started'
        def launch(*args):
            nonlocal child
            result = original(*args)
            script = "import sys,json; from pathlib import Path; from workspace_lifecycle import project_gate; "
            script += "config=json.loads(sys.argv[1]); gate=project_gate(config,'TST'); gate.__enter__(); Path(sys.argv[2]).write_text('started'); gate.__exit__(None,None,None)"
            child = subprocess.Popen([sys.executable, '-c', script, json.dumps(self.config), str(marker)],
                                     env=dict(os.environ, PYTHONPATH=str(Path(cleanup.__file__).parent)))
            with self.assertRaises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)
            self.assertFalse(marker.exists())
            return result
        with patch.object(cleanup, 'archive', launch):
            self.assertEqual(self.clean(record), 'cleaned')
        child.wait(timeout=10)
        self.assertTrue(marker.exists())


    def test_waiting_review_failed_and_running_remain(self):
        self.worktree('repo'); record = self.owned()
        self.platform.issues[1]['columnId'] = 3
        self.assertEqual(self.clean(record), 'retained:main-task-not-Done')
        self.platform.issues[1]['columnId'] = 4
        self.platform.issues[1]['subtasks'] = [{'id': 2}]
        self.platform.issues[2] = {'id': 2, 'projectId': 1, 'columnId': 2, 'parentId': 1,
                                  'statusSince': '2020-01-01T00:00:00Z', 'subtasks': []}
        self.assertEqual(self.clean(record), 'retained:review-or-subtask-not-Done')
        self.platform.issues[1]['subtasks'] = []
        self.platform.queue[0]['status'] = 'failed'
        self.assertEqual(self.clean(record), 'retained:latest-related-run-not-successful')
        self.platform.queue[0]['status'] = 'pending'
        self.assertEqual(self.clean(record), 'retained:related-run-active-or-pending')
        self.platform.queue[0]['status'] = 'success'
        with lease(self.config, 'TST', '1'):
            self.assertEqual(self.clean(record), 'retained:execution-lock')
        self.assertTrue(self.root.exists())

    def test_sensitive_material_preserved_not_archived(self):
        checkout = self.worktree('repo'); record = self.owned()
        (checkout / '.env').write_text('API_KEY=not-a-real-secret\n')
        with self.assertRaisesRegex(RuntimeError, 'sensitive'):
            self.clean(record)
        self.assertTrue((checkout / '.env').exists())
        self.assertFalse((self.base / 'archives').exists())

    def nested(self, linked):
        child_source = self.source('child-original')
        parent = self.source('parent')
        git(parent, '-c', 'protocol.file.allow=always', 'submodule', 'add', str(child_source), 'child')
        git(parent, 'commit', '-m', 'Fixture submodule')
        parent_wt = self.worktree('parent', parent)
        if linked:
            source = parent / 'child'
            self.registry['TST']['repositories'].append({'name': 'child', 'path': str(source),
                                                        'origin': git(source, 'remote', 'get-url', 'origin')})
            child = self.worktree('child', source, parent_wt / 'child')
        else:
            git(parent_wt, '-c', 'protocol.file.allow=always', 'submodule', 'update', '--init')
            child = parent_wt / 'child'
        (child / 'code.txt').write_text('submodule uncommitted\n')
        return parent_wt, child

    def test_nested_linked_child_removed_before_parent(self):
        _, child = self.nested(True); record = self.owned()
        self.assertEqual(self.clean(record), 'cleaned')
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        self.assertEqual(saved['removedRepositories'][0], str(child))
        self.assertFalse(self.root.exists())
        self.assertTrue(all(not registered(r['source'], r['path']) for r in self.repos))
        manifest = verify_archive(saved['archives'][0])
        self.assertTrue(any(r['path'] == str(child) for r in manifest['repositories']))

    def test_initialized_ordinary_submodule_archived_and_removed(self):
        _, child = self.nested(False); record = self.owned()
        self.assertEqual(self.clean(record), 'cleaned')
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        manifest = verify_archive(saved['archives'][0])
        sub = next(r for r in manifest['repositories'] if r['path'] == str(child))
        self.assertIn(b'submodule uncommitted', (Path(saved['archives'][0]) / sub['archiveDirectory'] / 'working.patch').read_bytes())
        self.assertFalse(self.root.exists())

    def test_partial_failure_keeps_archive_then_retries(self):
        self.worktree('one'); self.worktree('two'); record = self.owned()
        original = cleanup.git
        count = 0
        def fail_second(path, *args, **kw):
            nonlocal count
            if args[:2] == ('worktree', 'remove'):
                count += 1
                if count == 2:
                    raise RuntimeError('Injected Git removal failure')
            return original(path, *args, **kw)
        with patch.object(cleanup, 'git', fail_second):
            with self.assertRaisesRegex(RuntimeError, 'Injected'):
                self.clean(record)
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        verify_archive(saved['archives'][0])
        self.assertEqual(self.clean(record), 'cleaned')
        self.assertFalse(self.root.exists())

    def test_final_state_reopened_during_archive_preserves_worktrees(self):
        self.worktree('repo'); record = self.owned(); original = cleanup.archive
        def reopen(*args):
            result = original(*args)
            self.platform.issues[1]['columnId'] = 3
            return result
        with patch.object(cleanup, 'archive', reopen):
            self.assertEqual(self.clean(record), 'retained:final-state-changed')
        self.assertTrue(self.root.exists())

    def test_unknown_nested_checkout_preserved(self):
        parent = self.worktree('parent')
        unknown = parent / 'unknown'; unknown.mkdir()
        git(unknown, 'init')
        record = self.owned()
        with self.assertRaisesRegex(RuntimeError, 'Unowned nested'):
            self.clean(record)
        self.assertTrue(unknown.exists())

    def test_committed_unmerged_history_bundled_branch_retained(self):
        wt = self.worktree('repo'); record = self.owned()
        (wt / 'code.txt').write_text('unmerged commit\n')
        git(wt, 'add', 'code.txt'); git(wt, 'commit', '-m', 'Unmerged fixture')
        head = git(wt, 'rev-parse', 'HEAD')
        self.assertEqual(self.clean(record), 'cleaned')
        saved = json.loads(record_path(self.config, 'TST', '1').read_text())
        bundles = list(Path(saved['archives'][0]).glob('repo-*/commits.bundle'))
        self.assertEqual(len(bundles), 1)
        git(record['repositories'][0]['source'], 'bundle', 'verify', str(bundles[0]))
        self.assertEqual(git(record['repositories'][0]['source'], 'rev-parse', 'fixture/tst-1'), head)


    def test_invalid_dependency_maps_fail_closed(self):
        from workspace_lifecycle import validate_config
        for mapping in ({'1': [1]}, {'1': [2], '2': [3]}, {'1': [3], '2': [3]}, {'1': ['2']}):
            with self.assertRaises(RuntimeError):
                validate_config(dict(self.config, issueDependencies=mapping))

    def test_dependency_project_mismatch_is_retained(self):
        self.worktree('repo'); record = self.owned()
        self.config['issueDependencies'] = {'1': [2]}
        self.platform.issues[2] = dict(self.platform.issues[1], id=2, projectId=99)
        self.assertEqual(self.clean(record), 'retained:dependency-project-mismatch')
        self.assertTrue(self.root.exists())

    def test_missing_run_evidence_is_retained(self):
        self.worktree('repo'); record = self.owned()
        self.platform.queue = []
        self.assertEqual(self.clean(record), 'retained:related-run-evidence-missing')
        self.assertTrue(self.root.exists())

    def test_recent_dependency_done_is_retained(self):
        from datetime import datetime, timezone
        self.worktree('repo'); record = self.owned()
        self.config['quietSeconds'] = 30
        self.config['issueDependencies'] = {'1': [2]}
        self.platform.issues[2] = dict(self.platform.issues[1], id=2,
            statusSince=datetime.now(timezone.utc).isoformat())
        self.assertEqual(self.clean(record), 'retained:dependency-completion-settle-period')
        self.assertTrue(self.root.exists())

    def test_legacy_protection_is_retained(self):
        self.worktree('repo'); record = self.owned()
        self.config['retainedIssueIds'] = [1]
        self.assertEqual(self.clean(record), 'retained:explicit-legacy-protection')
        self.assertTrue(self.root.exists())

    def test_cleanup_requires_explicit_enable(self):
        for enabled in (None, False, 'true', 1):
            config = dict(self.config)
            if enabled is not None:
                config['enabled'] = enabled
            else:
                config.pop('enabled', None)
            self.assertEqual(cleanup.sweep(config=config, registry=self.registry, platform=self.platform), [{'outcome': 'disabled'}])

    def test_sweep_refreshes_config_after_lock(self):
        self.worktree('repo'); self.owned()
        old = dict(self.config, enabled=True)
        fresh = dict(old, issueDependencies={'1': [2]})
        with patch.object(cleanup, 'read_config', side_effect=[old, fresh]), \
             patch.object(cleanup, 'Platform', return_value=self.platform), \
             patch.object(cleanup, '_sweep', return_value=[]) as sweep:
            cleanup.sweep(registry=self.registry)
        self.assertEqual(sweep.call_args.args[0]['issueDependencies'], {'1': [2]})

    def test_dependency_helper_registers_only_open_main(self):
        import workspace_dependency as helper
        config_path = self.base / 'cleanup.json'
        self.platform.issues[2] = dict(self.platform.issues[1], id=2, columnId=2)
        with patch.object(helper, 'read_config', return_value=self.config), \
             patch.object(helper, 'Platform', return_value=self.platform), \
             patch.object(helper, 'CONFIG', config_path):
            with self.assertRaisesRegex(RuntimeError, 'still open'):
                helper.add_dependency('TST', 1, 2)
            self.platform.issues[1]['columnId'] = 3
            helper.add_dependency('TST', 1, 2)
        self.assertEqual(json.loads(config_path.read_text())['issueDependencies'], {'1': [2]})


if __name__ == '__main__':
    unittest.main(verbosity=2)
