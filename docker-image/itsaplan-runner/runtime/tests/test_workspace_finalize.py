"""Native Git and protocol tests for Agent-owned event-driven finalization."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_workspace_cleanup as fixtures
import workspace_finalize as finalize
from workspace_lifecycle import atomic_json, project_gate, record_path


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CleanupTests()
        self.f.setUp()
        self.f.worktree('repo')
        self.f.owned()
        self.f.platform.issues[1]['delegateUserId'] = 'agent-fixture'
        original = self.f.platform.project
        def project(key):
            value = original(key)
            value['columns'].append({'id': 9, 'stateType': 'canceled'})
            value['assignees'] = [{'kind': 'agent', 'name': 'Atlas', 'userId': 'agent-fixture'}]
            return value
        self.f.platform.project = project
        self.f.platform.call = lambda *args: [{'id': 1, 'name': 'Atlas'}]
        self.env = patch.dict(os.environ, {'ITSAPLAN_PROJECT_KEY': 'TST', 'ITSAPLAN_RUN_ID': '1',
                    'ITSAPLAN_ISSUE_ID': '1', 'ITSAPLAN_AGENT_NAME': 'Atlas',
                    'ITSAPLAN_API_KEY': 'synthetic-unit-identity', 'ITSAPLAN_URL': 'https://example.invalid'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.config_patch = patch.object(finalize, 'read_config', return_value=self.f.config)
        self.config_patch.start(); self.addCleanup(self.config_patch.stop)
        self.platform_patch = patch.object(finalize, 'agent_platform', return_value=self.f.platform)
        self.platform_patch.start(); self.addCleanup(self.platform_patch.stop)
        self.comments = patch.object(finalize, 'post_comment')
        self.comment_mock = self.comments.start(); self.addCleanup(self.comments.stop)
        self.registry_path = self.f.base / 'registry.json'
        atomic_json(self.registry_path, self.f.registry)
        self.registry_patch = patch.object(finalize, 'REGISTRY', self.registry_path)
        self.registry_patch.start(); self.addCleanup(self.registry_patch.stop)
        self.invocation = finalize.Invocation(self.f.config, 'TST', '1')

    def accept(self, reason=''):
        finalize.request(1, reason)
        return self.invocation.child_ended(0)

    def result(self, request):
        return self.invocation.finalize(request)['outcomes']

    def test_no_agent_request_preserves_workspace(self):
        self.assertIsNone(self.invocation.child_ended(0))
        self.assertTrue(self.f.root.exists())
        self.assertFalse((Path(self.f.config['stateRoot']) / 'finalize-events/1.json').exists())

    def test_done_current_running_then_native_removal(self):
        self.f.platform.queue[0]['status'] = 'running'
        request = self.accept()
        self.assertEqual(self.result(request), [{'runId': '1', 'outcome': 'cleaned'}])
        self.assertFalse(self.f.root.exists())
        self.assertEqual(self.result(request), [{'runId': '1', 'outcome': 'already-cleaned'}])

    def test_canceled_preserves_reason_and_dirty_patch(self):
        self.f.platform.issues[1]['columnId'] = 9
        self.f.platform.queue[0]['status'] = 'running'
        (self.f.root / 'repo/code.txt').write_text('canceled progress\n')
        with self.assertRaisesRegex(RuntimeError, 'reason'):
            finalize.request(1, '')
        request = self.accept('需求取消，保留目前成果')
        self.assertEqual(self.result(request), [{'runId': '1', 'outcome': 'cleaned'}])
        record = json.loads(record_path(self.f.config, 'TST', '1').read_text())
        self.assertEqual(record['agentFinalizeRequest']['reason'], '需求取消，保留目前成果')
        patch_path = Path(record['archives'][0]) / 'repo-0/working.patch'
        self.assertIn(b'canceled progress', patch_path.read_bytes())

    def test_child_failure_never_finalizes(self):
        finalize.request(1, '')
        self.assertIsNone(self.invocation.child_ended(1))
        self.assertTrue(self.f.root.exists())

    def test_request_comment_failure_has_no_accepted_request(self):
        self.comment_mock.side_effect = RuntimeError('Injected report failure')
        with self.assertRaisesRegex(RuntimeError, 'Injected'):
            finalize.request(1, '')
        self.assertFalse((self.invocation.folder / 'request.json').exists())

    def test_changed_owner_or_terminal_preserves(self):
        request = self.accept()
        self.f.platform.issues[1]['delegateUserId'] = 'someone-else'
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:main-owner-or-terminal-changed')
        self.f.platform.issues[1]['delegateUserId'] = 'agent-fixture'
        self.f.platform.issues[1]['columnId'] = 9
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:main-owner-or-terminal-changed')
        self.assertTrue(self.f.root.exists())

    def test_official_failed_current_run_is_not_overridden(self):
        request = self.accept()
        self.f.platform.queue[0]['status'] = 'failed'
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:official-current-run-conflict')
        self.assertTrue(self.f.root.exists())

    def test_waiting_review_and_other_active_request_rejected(self):
        self.f.config['issueDependencies'] = {'1': [2]}
        self.f.platform.issues[2] = dict(self.f.platform.issues[1], id=2, columnId=3)
        with self.assertRaisesRegex(RuntimeError, 'still open'):
            finalize.request(1, '')
        self.f.platform.issues[2]['columnId'] = 4
        self.f.platform.queue.append({'id': 2, 'issueId': 2, 'status': 'running', '_agentId': 1})
        with self.assertRaisesRegex(RuntimeError, 'active or queued'):
            finalize.request(1, '')
        self.assertTrue(self.f.root.exists())

    def test_new_related_success_since_request_retained(self):
        request = self.accept()
        self.f.platform.queue.append({'id': 99, 'issueId': 1, 'status': 'success', '_agentId': 1})
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:related-runs-changed-since-request')
        self.assertTrue(self.f.root.exists())

    def test_active_project_gate_not_skipped(self):
        request = self.accept()
        with project_gate(self.f.config, 'TST'):
            self.assertEqual(self.result(request)[0]['outcome'], 'retained:project-execution-active')
        self.assertTrue(self.f.root.exists())

    def test_nonce_mismatch_cannot_finalize(self):
        finalize.request(1, '')
        path = self.invocation.folder / 'request.json'
        value = json.loads(path.read_text()); value['token'] = 'invalid'
        atomic_json(path, value)
        with self.assertRaisesRegex(RuntimeError, 'capability mismatch'):
            self.invocation.child_ended(0)
        self.assertTrue(self.f.root.exists())

    def test_finalization_reason_secret_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'sensitive'):
            finalize.request(1, 'sk-' + 'a' * 30)
        self.assertFalse((self.invocation.folder / 'request.json').exists())

    def test_disabled_after_request_retains(self):
        request = self.accept()
        self.f.config['enabled'] = False
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:disabled')
        self.assertTrue(self.f.root.exists())

    def test_new_open_dependency_after_request_retains(self):
        request = self.accept()
        self.f.config['issueDependencies'] = {'1': [2]}
        self.f.platform.issues[2] = dict(self.f.platform.issues[1], id=2, columnId=3)
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:review-or-subtask-not-Done')
        self.assertTrue(self.f.root.exists())

    def test_own_run_evidence_required(self):
        self.f.platform.queue = [{'id': 99, 'issueId': 1, 'status': 'success', '_agentId': 1}]
        request = self.accept()
        self.assertEqual(self.result(request)[0]['outcome'], 'retained:official-current-run-conflict')
        self.assertTrue(self.f.root.exists())


    def test_final_report_failure_keeps_actual_cleanup_receipt(self):
        request = self.accept()
        self.comment_mock.side_effect = RuntimeError('Injected final report failure')
        value = self.invocation.finalize(request)
        self.assertFalse(value['reportDelivered'])
        self.assertEqual(value['outcomes'], [{'runId': '1', 'outcome': 'cleaned'}])
        self.assertFalse(self.f.root.exists())
        saved = json.loads((Path(self.f.config['stateRoot']) / 'finalize-events/1.json').read_text())
        self.assertEqual(saved['outcomes'], value['outcomes'])


    def test_review_task_cannot_request_main_finalization(self):
        self.f.config['issueDependencies'] = {'2': [1]}
        with self.assertRaisesRegex(RuntimeError, 'review dependency'):
            finalize.request(1, '')
        self.assertTrue(self.f.root.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
