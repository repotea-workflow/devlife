import sys,json,unittest,importlib
from pathlib import Path
from unittest.mock import patch
import workspace_recover as recovery
import test_workspace_cleanup as fixtures
from workspace_lifecycle import record_path,atomic_json
from workspace_archive import verify_archive
class AliasRecoveryTests(unittest.TestCase):
 def setup_archive(self):
  f=fixtures.CleanupTests();f.setUp();f.worktree('repo');record=f.owned();self.assertEqual(f.clean(record),'cleaned');record=json.loads(record_path(f.config,'TST','1').read_text());snapshot=Path(record['archives'][0]);original=f.registry['TST']['repositories'][0]['path'];new=f.base/'sources/relocated-repo';Path(original).rename(new);f.registry['TST']['repositories'][0]['path']=str(new);f.registry['TST']['sourceAliases']={original:str(new)};config=f.base/'registry-alias.json';atomic_json(config,f.registry);return f,snapshot,original,new,config
 def test_old_archive_recovers_from_explicit_verified_alias(self):
  f,snapshot,old,new,config=self.setup_archive();before=verify_archive(snapshot)['fingerprint']
  with patch.object(recovery,'REGISTRY',config):r=recovery.recover(snapshot,f.base/'alias-recovery')
  self.assertEqual(r['repositoriesVerified'],1);self.assertEqual(r['sourceRemappings'],[{'originalSource':old,'currentSource':str(new)}]);self.assertEqual(verify_archive(snapshot)['fingerprint'],before)
 def test_alias_origin_mismatch_rejected(self):
  f,snapshot,old,new,config=self.setup_archive();f.registry['TST']['repositories'][0]['origin']='invalid-fixture-origin';atomic_json(config,f.registry)
  with patch.object(recovery,'REGISTRY',config),self.assertRaisesRegex(RuntimeError,'origin'):recovery.recover(snapshot,f.base/'wrong-origin-recovery')
 def test_symlink_alias_rejected(self):
  f,snapshot,old,new,config=self.setup_archive();link=f.base/'sources/source-link';link.symlink_to(new);f.registry['TST']['sourceAliases'][old]=str(link);f.registry['TST']['repositories'][0]['path']=str(link);atomic_json(config,f.registry)
  with patch.object(recovery,'REGISTRY',config),self.assertRaisesRegex(RuntimeError,'canonical'):recovery.recover(snapshot,f.base/'symlink-recovery')
 def test_traversal_alias_rejected(self):
  f,snapshot,old,new,config=self.setup_archive();target=str(new.parent/'..'/'sources'/new.name);f.registry['TST']['sourceAliases'][old]=target;f.registry['TST']['repositories'][0]['path']=target;atomic_json(config,f.registry)
  with patch.object(recovery,'REGISTRY',config),self.assertRaisesRegex(RuntimeError,'canonical'):recovery.recover(snapshot,f.base/'traversal-recovery')
 def test_wrong_named_same_origin_repository_rejected(self):
  f,snapshot,old,new,config=self.setup_archive();other=f.base/'sources/other-repo';import subprocess;subprocess.run(['git','clone','--quiet',str(new),str(other)],check=True);origin=f.registry['TST']['repositories'][0]['origin'];subprocess.run(['git','-C',str(other),'remote','set-url','origin',origin],check=True);f.registry['TST']['repositories'].append({'name':'other','path':str(other),'origin':origin});f.registry['TST']['sourceAliases'][old]=str(other);atomic_json(config,f.registry)
  with patch.object(recovery,'REGISTRY',config),self.assertRaisesRegex(RuntimeError,'named canonical'):recovery.recover(snapshot,f.base/'wrong-name-recovery')
if __name__=='__main__':unittest.main()
