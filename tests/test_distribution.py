"""Portable installer regressions: standard library only, exclusively synthetic roots."""
import hashlib,http.client,http.server,io,json,os,sqlite3,sys,tempfile,threading,unittest,zipfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import distribution as d
import manage

class Fixture(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name).resolve();self.root=self.base/'source';self.root.mkdir()
  for name,value in {'package.json':json.dumps({'version':'0.1.0'}),'requirements.lock':'example-package==1.0\n','.env.example':'MODEL_API_KEY=\nMODEL_CALLS_ENABLED=false\nMODEL_BUDGET_CNY=99\n','scripts/manage.py':'# test fixture\n','scripts/distribution.py':'# test fixture\n','apps/web/dist/index.html':'<h1>TEST</h1>','apps/api/app/main.py':'# API\n','README.md':'synthetic README'}.items():
   path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8')
 def package(self,kind='macos-install'):return d.build(self.root,self.base/'assets',kind)

class DistributionTests(Fixture):
 def test_standard_acceptance_suites_are_shipped_and_experiments_excluded(self):
  directory=self.root/'benchmarks';directory.mkdir()
  expected={'todo-v1','todo-v2','notes-v1','expenses-v1','habits-v1','reading-v1'}
  for name in expected|{'private-experiment'}:(directory/(name+'.spec.ts')).write_text('// synthetic suite')
  for kind in ['source','macos-install','windows-install']:
   with zipfile.ZipFile(self.package(kind)) as archive:
    shipped={name for name in archive.namelist() if name.startswith('benchmarks/')}
    self.assertEqual(shipped,{'benchmarks/'+name+'.spec.ts' for name in expected})
 def test_source_omits_built_frontend(self):
  with zipfile.ZipFile(self.package('source')) as archive:self.assertNotIn('apps/web/dist/index.html',archive.namelist())
 def test_installers_include_built_assets_and_platform(self):
  for kind in ['macos-install','windows-install']:
   package=self.package(kind);value=d.inspect(package);self.assertEqual(value['kind'],kind)
   with zipfile.ZipFile(package) as archive:self.assertIn('apps/web/dist/index.html',archive.namelist())
 def test_user_state_and_env_are_excluded(self):
  (self.root/'.env').write_text('MODEL_API_KEY=synthetic-private-key-abc\n')
  for name in ['data/private.txt','outputs/private.txt','generated-projects/example/src/secret.py','.git/config','node_modules/file.js','AGENTS.md']:
   p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic private')
  with zipfile.ZipFile(self.package()) as archive:
   names=archive.namelist();self.assertIn('.env.example',names);self.assertFalse(any(n.startswith(('data/','outputs/','generated-projects/','.git/','node_modules/')) or n in {'.env','AGENTS.md'} for n in names))
 def test_known_configured_secret_in_source_blocks_packaging(self):
  (self.root/'.env').write_text('MODEL_API_KEY=synthetic-private-key-abc\n');(self.root/'apps/api/app/main.py').write_text('synthetic-private-key-abc')
  with self.assertRaises(d.DistributionError):self.package()
 def test_nonempty_example_key_blocks_packaging(self):
  (self.root/'.env.example').write_text('MODEL_API_KEY=bad-example-key\n')
  with self.assertRaises(d.DistributionError):self.package()
 def test_invalid_version_and_existing_artifact_are_rejected(self):
  with self.assertRaises(d.DistributionError):d.build(self.root,self.base/'assets',release_version='../../escape')
  self.package()
  with self.assertRaises(d.DistributionError):self.package()
 def test_project_license_is_retained_when_provided(self):
  (self.root/'LICENSE').write_text('synthetic license text')
  with zipfile.ZipFile(self.package('source')) as archive:self.assertEqual(archive.read('LICENSE'),b'synthetic license text')
 def test_missing_frontend_rejects_installer(self):
  (self.root/'apps/web/dist/index.html').unlink()
  with self.assertRaises(d.DistributionError):self.package()
 def test_tampered_source_is_rejected(self):
  original=self.package();tampered=self.base/'bad.zip'
  with zipfile.ZipFile(original) as old,zipfile.ZipFile(tampered,'w') as new:
   for name in old.namelist():new.writestr(name,b'changed' if name=='scripts/manage.py' else old.read(name))
  with self.assertRaises(d.DistributionError):d.inspect(tampered)
 def test_archive_traversal_or_undeclared_entry_is_rejected(self):
  original=self.package();tampered=self.base/'bad.zip'
  with zipfile.ZipFile(original) as old,zipfile.ZipFile(tampered,'w') as new:
   for name in old.namelist():new.writestr(name,old.read(name))
   new.writestr('../escape.py','bad')
  with self.assertRaises(d.DistributionError):d.extract(tampered,self.base/'unpacked')
  self.assertFalse((self.base/'escape.py').exists())
 def test_extract_never_overwrites_destination(self):
  package=self.package();dest=self.base/'unpacked';d.extract(package,dest)
  with self.assertRaises(d.DistributionError):d.extract(package,dest)
 def test_upgrade_plan_does_not_modify_source_or_create_destination(self):
  data=self.root/'data';data.mkdir();(data/'record.txt').write_text('preserved');package=self.package();dest=self.base/'new'
  plan=d.upgrade(self.root,package,dest);self.assertFalse(plan['applied']);self.assertFalse(dest.exists());self.assertEqual((data/'record.txt').read_text(),'preserved')
 def test_side_by_side_upgrade_preserves_keys_sqlite_and_source(self):
  data=self.root/'data';data.mkdir();(self.root/'.env').write_text('MODEL_API_KEY=synthetic-private-key-abc\n')
  with closing(sqlite3.connect(data/'sample.sqlite3')) as conn,conn:conn.execute('CREATE TABLE sample(value TEXT)');conn.execute("INSERT INTO sample VALUES ('unchanged')")
  workspace=self.root/'generated-projects/fixture';workspace.mkdir(parents=True);workspace.chmod(0o777);(workspace/'source.txt').write_text('old source');(workspace/'source.txt').chmod(0o666)
  original=(data/'sample.sqlite3').read_bytes();package=self.package();dest=self.base/'new';result=d.upgrade(self.root,package,dest,True)
  self.assertTrue(result['applied']);self.assertEqual((dest/'.env').read_bytes(),(self.root/'.env').read_bytes());self.assertEqual((data/'sample.sqlite3').read_bytes(),original)
  with closing(sqlite3.connect(dest/'data/sample.sqlite3')) as conn:self.assertEqual(conn.execute('SELECT value FROM sample').fetchone()[0],'unchanged')
  (dest/'generated-projects/fixture/source.txt').write_text('new source');self.assertEqual((workspace/'source.txt').read_text(),'old source')
  if os.name!='nt':self.assertEqual((dest/'generated-projects/fixture/source.txt').stat().st_mode&0o777,0o666)
 def test_upgrade_refuses_live_process_and_low_disk(self):
  (self.root/'data').mkdir();(self.root/'data/api.pid').write_text(str(os.getpid()))
  with self.assertRaises(d.DistributionError):d.upgrade(self.root,self.package(),self.base/'new',True)
 def test_upgrade_refuses_nested_destination(self):
  with self.assertRaises(d.DistributionError):d.upgrade(self.root,self.package(),self.root/'nested',True)
 @unittest.skipIf(os.name=='nt','Windows symlink creation may require developer mode')
 def test_symlinked_source_is_rejected(self):
  (self.root/'scripts/link.py').symlink_to(self.root/'scripts/manage.py')
  with self.assertRaises(d.DistributionError):self.package()
 def test_failed_upgrade_leaves_no_new_directory_or_changes(self):
  (self.root/'data').mkdir();(self.root/'data/bad.sqlite3').write_bytes(b'not sqlite');package=self.package();dest=self.base/'new'
  with self.assertRaises(sqlite3.DatabaseError):d.upgrade(self.root,package,dest,True)
  self.assertFalse(dest.exists());self.assertEqual((self.root/'data/bad.sqlite3').read_bytes(),b'not sqlite')
 def test_private_hidden_paths_and_windows_drives_are_rejected(self):
  for name in ['C:/file.py','scripts/.private/file.py','scripts/a:bad/file.py','/escape.py','scripts/../escape.py']:self.assertFalse(d.public_path(name,'source'))

class ConfigurationTests(Fixture):
 def test_config_initialization_is_idempotent_and_off_by_default(self):
  self.assertTrue(manage.init_config(self.root));manage.save_key(self.root,'deepseek','synthetic-user-key-abc');self.assertFalse(manage.init_config(self.root))
  config=manage.read_config(self.root);self.assertEqual(config['MODEL_CALLS_ENABLED'],'false');self.assertEqual(config['MODEL_API_KEY'],'synthetic-user-key-abc')
 def test_config_check_never_returns_key_values(self):
  manage.save_key(self.root,'deepseek','synthetic-user-key-abc');value=manage.check_config(self.root)
  self.assertNotIn('synthetic-user-key',json.dumps(value));self.assertTrue(value['budget_valid']);self.assertEqual(value['network_requests'],0)
 def test_invalid_key_does_not_change_config(self):
  manage.init_config(self.root);before=(self.root/'.env').read_bytes()
  with self.assertRaises(d.DistributionError):manage.save_key(self.root,'deepseek','abc\nMODEL_CALLS_ENABLED=true')
  self.assertEqual((self.root/'.env').read_bytes(),before)
 def test_install_plan_uses_prebuilt_frontend_without_node_install(self):
  value=manage.install(self.root,plan=True);self.assertTrue(value['prebuilt_frontend']);self.assertFalse(any(c[0]=='npm' for c in value['commands']));self.assertFalse((self.root/'.env').exists())

class StaticServerTests(unittest.TestCase):
 def test_only_public_dist_files_are_served(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'index.html').write_text('public UI');(root/'.env').write_text('synthetic-private')
   handler=lambda *args,**kw:manage.Frontend(*args,directory=tmp,**kw)
   server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    client=http.client.HTTPConnection('127.0.0.1',server.server_port)
    for path,host,status in [('/','127.0.0.1:5173',200),('/.env','127.0.0.1:5173',404),('/%2e%2e/secret','127.0.0.1:5173',404),('/','attacker.example',400)]:
     client.request('GET',path,headers={'Host':host});response=client.getresponse();body=response.read();self.assertEqual(response.status,status)
     if status==200:self.assertEqual(body,b'public UI');self.assertEqual(response.getheader('Cache-Control'),'no-store')
    client.close()
   finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
