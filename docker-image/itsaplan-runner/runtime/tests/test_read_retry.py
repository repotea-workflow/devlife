import subprocess,unittest
from unittest.mock import patch
from workspace_lifecycle import Platform
class ReadRetryTests(unittest.TestCase):
 def result(self,code,stderr='',stdout=''):return subprocess.CompletedProcess([],code,stdout,stderr)
 def test_transient_read_recovers_with_bounded_delay(self):
  with patch('workspace_lifecycle.subprocess.run',side_effect=[self.result(1,'spec discovery failed: context deadline exceeded'),self.result(1,'timed out'),self.result(0,stdout='{"id":27}')]) as run,patch('workspace_lifecycle.time.sleep') as sleep:
   self.assertEqual(Platform({}).call('issues','get-issues-by-issue-id',27),{'id':27});self.assertEqual(run.call_count,3);self.assertEqual(sleep.call_args_list, [((5,),),((5,),)])
 def test_persistent_timeout_stops_at_three(self):
  with patch('workspace_lifecycle.subprocess.run',return_value=self.result(1,'context deadline exceeded')) as run,patch('workspace_lifecycle.time.sleep') as sleep:
   with self.assertRaisesRegex(RuntimeError,'exit=1'):Platform({}).call('issues','get-issues-by-issue-id',27)
   self.assertEqual(run.call_count,3);self.assertEqual(sleep.call_count,2)
 def test_auth_failure_not_retried(self):
  for diagnostic in ['unauthorized', 'spec discovery failed: 401 unauthorized', 'context deadline exceeded; forbidden', 'spec discovery failed; permission denied', 'timed out; Invalid API key']:
   with self.subTest(diagnostic=diagnostic),patch('workspace_lifecycle.subprocess.run',return_value=self.result(1,diagnostic)) as run,patch('workspace_lifecycle.time.sleep') as sleep:
    with self.assertRaises(RuntimeError):Platform({}).call('issues','get-issues-by-issue-id',27)
    self.assertEqual(run.call_count,1);sleep.assert_not_called()
 def test_write_not_retried(self):
  with patch('workspace_lifecycle.subprocess.run',return_value=self.result(1,'spec discovery failed')) as run,patch('workspace_lifecycle.time.sleep') as sleep:
   with self.assertRaises(RuntimeError):Platform({}).call('issues','post-issues-by-issue-id-comments',27)
   self.assertEqual(run.call_count,1);sleep.assert_not_called()
 def test_process_timeout_recovers(self):
  failure=subprocess.TimeoutExpired(['itsaplan'],90,output=b'private response',stderr=b'')
  with patch('workspace_lifecycle.subprocess.run',side_effect=[failure,self.result(0,stdout='{"id":29}')]) as run,patch('workspace_lifecycle.time.sleep') as sleep:
   self.assertEqual(Platform({}).call('issues','get-issues-by-issue-id',29),{'id':29});self.assertEqual(run.call_count,2);sleep.assert_called_once_with(5)
 def test_process_timeout_exhausted_and_sanitized(self):
  failure=subprocess.TimeoutExpired(['itsaplan'],90,output=b'private response',stderr=b'private credential')
  with patch('workspace_lifecycle.subprocess.run',side_effect=failure) as run,patch('workspace_lifecycle.time.sleep') as sleep:
   with self.assertRaisesRegex(RuntimeError,'exit=124; stderr=timed out') as raised:Platform({}).call('issues','get-issues-by-issue-id',29)
   self.assertNotIn('private',str(raised.exception));self.assertEqual(run.call_count,3);self.assertEqual(sleep.call_count,2)
 def test_process_timeout_write_and_partial_auth_not_retried(self):
  for operation,stderr in [('post-issues-by-issue-id-comments',b''),('get-issues-by-issue-id',b'401 unauthorized')]:
   with self.subTest(operation=operation),patch('workspace_lifecycle.subprocess.run',side_effect=subprocess.TimeoutExpired(['itsaplan'],90,stderr=stderr)) as run,patch('workspace_lifecycle.time.sleep') as sleep:
    with self.assertRaises(RuntimeError):Platform({}).call('issues',operation,29)
    self.assertEqual(run.call_count,1);sleep.assert_not_called()
 def test_numeric_auth_status_without_phrase_not_retried(self):
  for code in [401,403]:
   with self.subTest(code=code),patch('workspace_lifecycle.subprocess.run',return_value=self.result(1,f'spec discovery failed: HTTP {code}')) as run,patch('workspace_lifecycle.time.sleep') as sleep:
    with self.assertRaises(RuntimeError):Platform({}).call('issues','get-issues-by-issue-id',29)
    self.assertEqual(run.call_count,1);sleep.assert_not_called()
if __name__=='__main__':unittest.main()
