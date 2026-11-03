"""Synthetic workbench data only; no paid API, real user backup, or host generated code."""
import asyncio,io,json,os,shutil,sqlite3,sys,tempfile,time,unittest,uuid,zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import workbench as w,workbench_archive as a,runner,model
PROJECT=Path(__file__).resolve().parents[1]

class Fixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'source';self.root.mkdir();self.id=str(uuid.uuid4())
  for name in ['scripts/distribution.py','apps/api/app/release_policy.py','scripts/manage.py','.env.example','requirements.lock','package.json']:
   dest=self.root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(PROJECT/name,dest)
  self.secret='synthetic-key-DO-NOT-EXPORT-abcdef'
  (self.root/'.env').write_text('MODEL_API_KEY='+self.secret+'\nMODEL_CALLS_ENABLED=true\nMODEL_BUDGET_CNY=17\nRAG_API_KEY=synthetic-rag-secret-12345\n')
  (self.root/'data').mkdir()
  self.db=sqlite3.connect(self.root/'data/runs.sqlite3');self.addCleanup(self.db.close)
  self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('CREATE TABLE runs(id TEXT,state TEXT)')
  self.state={'run_id':self.id,'status':'paused','stage':'executor','user_request':'SYNTHETIC PRIVATE REQUEST','last_error':'SYNTHETIC PRIVATE ERROR'}
  self.db.execute('INSERT INTO runs VALUES (?,?)',(self.id,json.dumps(self.state)));self.db.commit()
  self.write('data/projects/'+self.id+'/manifest.json','{"files":{}}')
  self.write('generated-projects/'+self.id+'/src/App.tsx','export default function App(){return null}')
  self.write('generated-projects/'+self.id+'/node_modules/DO_NOT_EXPORT','cache')
  self.write('data/knowledge/raw.txt','SYNTHETIC PRIVATE DOCUMENT')
  self.write('data/applications/'+self.id+'/attachment.txt','SYNTHETIC ATTACHMENT')
  self.write('data/git/'+self.id+'/HEAD','ref: refs/heads/main\n')
  self.write('data/git/'+self.id+'/config','[core]\n hooksPath=/PRIVATE/HOST\n')
  self.write('outputs/'+self.id+'-report.md','synthetic report')
  self.write('data/m19-private-lab/secret','not part of user workbench')
  self.write('data/api.log','RAW LOG '+self.secret)
  self.write('outputs/experiment-private.txt','EXPERIMENT')
  usage=self.root/'data/api_usage.sqlite3'
  with closing(sqlite3.connect(usage)) as c:c.execute('CREATE TABLE model_usage(cost REAL)');c.execute('INSERT INTO model_usage VALUES (1.75)');c.commit()
  self.patch=patch.object(runner,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
 def write(self,name,text):
  path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path
 def create(self):
  path=self.root/'backup.zip';a.create(self.root,path);return path
 def client(self):
  app=FastAPI();app.include_router(w.router)
  @app.exception_handler(a.ArchiveError)
  async def archive_error(request,error):
   from fastapi.responses import JSONResponse
   return JSONResponse(status_code=409,content={'detail':str(error)})
  return TestClient(app)

class ArchiveTests(Fixture):
 def test_scope_excludes_credentials_caches_logs_and_test_labs(self):
  path=self.create()
  with zipfile.ZipFile(path) as z:
   names=z.namelist();raw=b''.join(z.read(n) for n in names)
   self.assertNotIn(self.secret.encode(),raw)
   self.assertFalse(any('node_modules' in n or '.env' in n or 'm19-private' in n or n.endswith('.log') for n in names))
   self.assertNotIn('outputs/experiment-private.txt',names)
   self.assertIn('data/knowledge/raw.txt',names);self.assertIn('data/applications/'+self.id+'/attachment.txt',names)
 def test_sqlite_wal_rows_and_spend_survive_side_by_side_restore(self):
  path=self.create();result=a.restore(self.root,path,uuid.uuid4());target=Path(result['location'])
  self.assertFalse(result['executed']);self.assertTrue((self.root/'.env').is_file());self.assertNotIn(self.root,target.parents)
  with closing(sqlite3.connect(target/'data/runs.sqlite3')) as c:self.assertEqual(json.loads(c.execute('SELECT state FROM runs').fetchone()[0])['user_request'],self.state['user_request'])
  with closing(sqlite3.connect(target/'data/api_usage.sqlite3')) as c:self.assertEqual(c.execute('SELECT cost FROM model_usage').fetchone()[0],1.75)
  env=(target/'.env').read_text();self.assertIn('MODEL_API_KEY=\n',env);self.assertIn('MODEL_CALLS_ENABLED=false',env);self.assertIn('MODEL_BUDGET_CNY=17',env);self.assertNotIn(self.secret,env)
  self.assertEqual((target/'data/applications'/self.id/'attachment.txt').read_text(),'SYNTHETIC ATTACHMENT')
  self.assertNotIn('/PRIVATE/HOST',(target/'data/git'/self.id/'config').read_text())
  self.assertTrue((target/'scripts/manage.py').is_file())
  if os.name!='nt':self.assertEqual((target/'generated-projects'/self.id/'src/App.tsx').stat().st_mode&0o777,0o666)
 def test_idempotent_restore_never_overwrites_existing_copy(self):
  path=self.create();token=uuid.uuid4();first=a.restore(self.root,path,token);marker=Path(first['location'])/'keep';marker.write_text('unchanged')
  self.assertTrue(a.restore(self.root,path,token)['replayed']);self.assertEqual(marker.read_text(),'unchanged')
 def test_tamper_traversal_duplicate_or_incompatible_archive_rejected(self):
  path=self.create()
  with zipfile.ZipFile(path) as z:entries={n:z.read(n) for n in z.namelist()}
  for kind in ['tamper','traversal','duplicate','version']:
   altered=dict(entries);target=self.root/(kind+'.zip')
   if kind=='tamper':altered['settings.json']=b'{}'
   if kind=='traversal':
    m=json.loads(altered['WORKBENCH_MANIFEST.json']);value=b'bad';name='data/projects/../../.env';altered[name]=value;m['files'].append({'path':name,'bytes':3,'sha256':a.sha(value)});altered['WORKBENCH_MANIFEST.json']=json.dumps(m).encode()
   if kind=='version':
    m=json.loads(altered['WORKBENCH_MANIFEST.json']);m['app_version']='99.0.0';altered['WORKBENCH_MANIFEST.json']=json.dumps(m).encode()
   with zipfile.ZipFile(target,'w') as z:
    for name,value in altered.items():z.writestr(name,value)
    if kind=='duplicate':
     import warnings
     with warnings.catch_warnings():warnings.simplefilter('ignore');z.writestr('settings.json',b'{}')
   with self.subTest(kind=kind),self.assertRaises(a.ArchiveError):a.inspect(target,a.version(self.root))
 def test_known_configured_key_in_user_data_blocks_export_without_changing_data(self):
  self.write('data/knowledge/raw.txt','private credential '+self.secret)
  with self.assertRaises(a.ArchiveError):self.create()
  self.assertEqual((self.root/'data/knowledge/raw.txt').read_text(),'private credential '+self.secret)
  self.assertFalse((self.root/'backup.zip').exists())
 def test_active_run_blocks_snapshot(self):
  self.db.execute('UPDATE runs SET state=?',(json.dumps({**self.state,'status':'running'}),));self.db.commit()
  with self.assertRaises(a.ArchiveError):self.create()
  self.assertFalse((self.root/'backup.zip').exists())
 @unittest.skipIf(os.name=='nt','Creating symlinks may require developer mode')
 def test_symlink_inside_selected_data_is_rejected(self):
  (self.root/'data/knowledge/link.txt').symlink_to(self.root/'.env')
  with self.assertRaises(a.ArchiveError):self.create()
 def test_restore_failure_removes_only_temporary_copy(self):
  path=self.create();token=uuid.uuid4()
  with patch.object(a.os,'rename',side_effect=OSError('synthetic disk failure')):
   with self.assertRaises(OSError):a.restore(self.root,path,token)
  self.assertFalse((a.restore_home(self.root)/str(token)).exists());self.assertIn(self.secret,(self.root/'.env').read_text())
 @unittest.skipIf(os.name=='nt','Creating symlinks may require developer mode')
 def test_symlinked_sqlite_sidecar_and_restore_destination_are_rejected(self):
  path=self.create();token=uuid.uuid4();directory=a.restore_home(self.root);directory.mkdir(parents=True);(directory/str(token)).symlink_to(self.root/'data',target_is_directory=True)
  with self.assertRaises(a.ArchiveError):a.restore(self.root,path,token)
  fake=self.root/'data/api_usage.sqlite3-wal';fake.symlink_to(self.root/'.env')
  with self.assertRaises(a.ArchiveError):a.query(self.root,'data/api_usage.sqlite3','SELECT * FROM model_usage')
 def test_cross_platform_reserved_names_and_case_collisions_are_rejected(self):
  for value in ['data/knowledge/CON.txt','data/knowledge/test.','data/knowledge/nul']:
   with self.subTest(value=value):self.assertFalse(a.allowed(value))
  path=self.create()
  with zipfile.ZipFile(path) as z:entries={n:z.read(n) for n in z.namelist()}
  target=self.root/'case-collision.zip'
  with zipfile.ZipFile(target,'w') as z:
   for name,raw in entries.items():z.writestr(name,raw)
   z.writestr('SETTINGS.JSON',b'{}')
  with self.assertRaises(a.ArchiveError):a.inspect(target,a.version(self.root))
 def test_space_limit_refuses_backup_without_deleting_source(self):
  with patch.object(a.shutil,'disk_usage',return_value=type('Disk',(),{'free':1})()):
   with self.assertRaises(a.ArchiveError):self.create()
  self.assertTrue((self.root/'data/knowledge/raw.txt').is_file())

class ConfigurationTests(Fixture):
 def test_key_save_is_hidden_preserves_other_providers_and_requires_restart(self):
  with self.client() as c:
   result=c.post('/api/v1/workbench/setup/config',json={'provider':'deepseek','api_key':'synthetic-new-key-123456','enabled':False,'budget_cny':20})
   self.assertEqual(result.status_code,200);self.assertTrue(result.json()['restart_required']);self.assertNotIn('synthetic-new',result.text)
  text=(self.root/'.env').read_text();self.assertIn('RAG_API_KEY=synthetic-rag-secret-12345',text);self.assertIn('MODEL_CALLS_ENABLED=false',text)
 def test_invalid_key_or_optional_enable_never_mutates_config(self):
  before=(self.root/'.env').read_bytes()
  with self.client() as c:
   for payload in [{'provider':'deepseek','api_key':'key\nBAD=1'},{'provider':'qwen','api_key':'synthetic-key-12345','enabled':True},{'provider':'deepseek','budget_cny':float('inf')}]:
    if payload.get('budget_cny')==float('inf'):continue
    self.assertEqual(c.post('/api/v1/workbench/setup/config',json=payload).status_code,422)
  self.assertEqual((self.root/'.env').read_bytes(),before)
 def test_complete_setup_persists_only_after_local_checks_and_loaded_config_match(self):
  env={'MODEL_CALLS_ENABLED':'true','MODEL_API_KEY':self.secret,'MODEL_BUDGET_CNY':'17'}
  with patch.dict(os.environ,env),patch.object(w,'local_command',return_value=True),patch.object(runner,'worker_image_available',return_value=True),patch.object(w.shutil,'which',return_value='synthetic-tool'),self.client() as c:
   self.assertTrue(c.get('/api/v1/workbench/setup').json()['ready'])
   response=c.post('/api/v1/workbench/setup/complete');self.assertEqual(response.status_code,200,response.text)
  self.assertTrue(json.loads((self.root/'data/workbench/preferences.json').read_text())['setup_completed'])
 def test_setup_status_has_no_keys_and_never_calls_provider(self):
  with patch.object(w,'local_command',return_value=False),patch.object(model,'generate_json',side_effect=AssertionError('No model calls')):
   result=w.setup_status();self.assertNotIn(self.secret,json.dumps(result));self.assertEqual(result['provider_requests'],0)
   self.assertFalse(result['ready'])

class DiagnosticTests(Fixture):
 def test_diagnostic_positive_schema_excludes_private_content_and_paths(self):
  report=w.diagnostic_report();text=json.dumps(report)
  for value in [self.secret,str(self.root),'SYNTHETIC PRIVATE REQUEST','SYNTHETIC PRIVATE DOCUMENT','RAW LOG','PRIVATE/HOST']:self.assertNotIn(value,text)
  self.assertEqual(report['runs_by_status']['paused'],1);self.assertEqual(report['network_requests'],0)
 def test_diagnostics_handle_invalid_json_records_and_unreadable_database(self):
  self.db.execute('UPDATE runs SET state=?',('[]',));self.db.commit()
  self.assertEqual(w.diagnostic_report()['runs_by_status']['corrupt'],1)
  with patch.object(a,'query',side_effect=sqlite3.DatabaseError('PRIVATE PATH AND CONTENT')):
   report=w.diagnostic_report();self.assertEqual(report['database_health'],'unreadable');self.assertNotIn('PRIVATE PATH',json.dumps(report))
 def test_diagnostic_download_matches_preview_and_expiry_is_enforced(self):
  with self.client() as c:
   response=c.post('/api/v1/workbench/diagnostics/preview');self.assertEqual(response.status_code,200);preview=response.json()
   downloaded=c.get('/api/v1/workbench/diagnostics/'+preview['token']+'/download');self.assertEqual(downloaded.status_code,200)
   with zipfile.ZipFile(io.BytesIO(downloaded.content)) as z:self.assertEqual(json.loads(z.read('diagnostic.json')),preview['report'])
   path=self.root/'data/workbench/diagnostics'/(preview['token']+'.json');os.utime(path,(time.time()-700,time.time()-700));self.assertEqual(c.get('/api/v1/workbench/diagnostics/'+preview['token']+'/download').status_code,409)
 def test_backup_upload_confirmation_discard_and_expiry(self):
  path=self.create()
  with self.client() as c:
   response=c.post('/api/v1/workbench/backups/preview',content=path.read_bytes());self.assertEqual(response.status_code,200);token=response.json()['token']
   self.assertTrue(c.get('/api/v1/workbench/backups/previews').json()['items'])
   self.assertEqual(c.get('/api/v1/workbench/backups/preview/'+token).status_code,200)
   self.assertEqual(c.post('/api/v1/workbench/backups/restore',json={'token':token,'confirmed':False}).status_code,422)
   self.assertEqual(c.delete('/api/v1/workbench/backups/preview/'+token).status_code,200)
   self.assertEqual(c.post('/api/v1/workbench/backups/restore',json={'token':token,'confirmed':True}).status_code,409)

class MaintenanceTests(unittest.TestCase):
 def test_exclusive_snapshot_drains_existing_request_and_rejects_new_writes(self):
  async def scenario():
   started=asyncio.Event();finish=asyncio.Event();calls=[];sent=[]
   async def app(scope,receive,send):
    calls.append(scope['path'])
    if scope['path']=='/ongoing':started.set();await finish.wait()
    await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b'ok'})
   middleware=w.MaintenanceMiddleware(app)
   async def receive():return {'type':'http.request','body':b''}
   async def send(message):sent.append(message)
   first=asyncio.create_task(middleware({'type':'http','method':'GET','path':'/ongoing'},receive,send));await started.wait()
   backup=asyncio.create_task(middleware({'type':'http','method':'POST','path':'/api/v1/workbench/backups/export'},receive,send));await asyncio.sleep(.02)
   await middleware({'type':'http','method':'POST','path':'/new-write'},receive,send)
   self.assertNotIn('/new-write',calls);self.assertTrue(any(x.get('status')==503 for x in sent));self.assertNotIn('/api/v1/workbench/backups/export',calls)
   finish.set();await first;await backup;self.assertIn('/api/v1/workbench/backups/export',calls);self.assertFalse(middleware.exclusive)
  asyncio.run(scenario())

class CheckpointSafetyTests(unittest.TestCase):
 def test_restored_checkpoint_cannot_invoke_an_arbitrary_python_callable(self):
  import ormsgpack
  from app import graph
  from langgraph.checkpoint.serde.jsonplus import EXT_CONSTRUCTOR_SINGLE_ARG
  with tempfile.TemporaryDirectory() as tmp:
   workflow=graph.build_graph(Path(tmp)/'checkpoint.sqlite3')
   try:
    payload=ormsgpack.packb(ormsgpack.Ext(EXT_CONSTRUCTOR_SINGLE_ARG,ormsgpack.packb(('os','system','synthetic-never-execute'))))
    with patch('os.system') as command:
     self.assertEqual(workflow.checkpointer.serde.loads_typed(('msgpack',payload)),'synthetic-never-execute')
     command.assert_not_called()
    with self.assertRaises(NotImplementedError):workflow.checkpointer.serde.loads_typed(('pickle',b'not accepted'))
   finally:workflow.checkpointer.conn.close()

if __name__=='__main__':unittest.main()
