"""Native namespace checks; run with the final Docker security options."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_sandbox import cursor


@unittest.skipUnless(os.environ.get('ITSAPLAN_SANDBOX_TEST') == '1',
                     'Requires the deployment AppArmor/seccomp options')
class CursorBoundaryTests(unittest.TestCase):
    def test_tools_cannot_write_source_or_shared_profile(self):
        with tempfile.TemporaryDirectory(dir='/data/itsaplan/runner/acceptance') as folder:
            base = Path(folder)
            task, source, profile = (base / p for p in ('task', 'source', 'profile'))
            for path in (task, source, profile):
                path.mkdir()
            (profile / 'cli-config.json').write_text('{}')
            (profile / 'auth.json').write_text('{"fixture":"synthetic"}')
            command = ['/bin/sh', '-c',
                       'printf OK > proof; '
                       f'if printf BAD > {source}/forbidden; then exit 7; fi; '
                       f'if printf BAD > {profile}/forbidden; then exit 8; fi; '
                       'printf LOCAL > "$CURSOR_CONFIG_DIR/cli-config.json"; '
                       'mkdir -p "$XDG_CONFIG_HOME/aicli/itsaplan" "$XDG_CACHE_HOME/cursor-compile-cache"; '
                       'printf RUNTIME_ONLY > "$XDG_CACHE_HOME/cursor-compile-cache/example"; '
                       f'cmp {profile}/auth.json "$XDG_CONFIG_HOME/cursor/auth.json"']
            self.assertEqual(cursor(command, task, [], profile, None), 0)
            self.assertEqual((task / 'proof').read_text(), 'OK')
            self.assertFalse((source / 'forbidden').exists())
            self.assertFalse((profile / 'forbidden').exists())
            self.assertEqual((profile / 'cli-config.json').read_text(), '{}')
            self.assertFalse((task / 'cursor-compile-cache').exists())

    def test_own_finalize_channel_available_other_tmp_is_private(self):
        with tempfile.TemporaryDirectory(dir='/data/itsaplan/runner/acceptance') as folder, \
                tempfile.TemporaryDirectory() as current, tempfile.TemporaryDirectory() as other:
            task, profile = Path(folder) / 'task', Path(folder) / 'profile'
            task.mkdir(); profile.mkdir()
            protected = Path(other) / 'request.json'
            protected.write_text('OTHER_INVOCATION')
            command = ['/bin/sh', '-c',
                       'printf OWN_REQUEST > "$ITSAPLAN_FINALIZE_REQUEST_DIR/request.json"; '
                       f'test ! -e {protected} || exit 9; '
                       f'mkdir -p {other}; printf PRIVATE_COPY > {protected}']
            with patch.dict(os.environ, ITSAPLAN_FINALIZE_REQUEST_DIR=current):
                self.assertEqual(cursor(command, task, [], profile, None), 0)
            self.assertEqual((Path(current) / 'request.json').read_text(), 'OWN_REQUEST')
            self.assertEqual(protected.read_text(), 'OTHER_INVOCATION')
