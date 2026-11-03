"""Synthetic migration, cancellation and comparison evidence. No external API requests."""
import json,sqlite3,sys,tempfile,threading,time,unittest,uuid,zipfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import workbench_archive as a,workbench_jobs as jobs,regression,library,document_runtime
from test_workbench import Fixture

class MigrationTests(Fixture):
 def old_archive(self):
  with closing(sqlite3.connect(self.root/'data/knowledge/library.sqlite3')) as c:
   c.execute('CREATE TABLE documents(id TEXT,status TEXT)');c.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT)');c.execute("INSERT INTO jobs VALUES ('old','completed')");c.commit()
  path=self.create()
  with zipfile.ZipFile(path) as z:entries={n:z.read(n) for n in z.namelist()}
  m=json.loads(entries['WORKBENCH_MANIFEST.json']);m['app_version']='0.2.0';entries['WORKBENCH_MANIFEST.json']=json.dumps(m).encode()
  with zipfile.ZipFile(path,'w') as z:
   for name,data in entries.items():z.writestr(name,data)
  return path
 def test_additive_migration_keeps_original_archive_and_rows(self):
  path=self.old_archive();raw=path.read_bytes();preview=a.inspect(path,'0.3.0');self.assertTrue(preview['compatibility']['migration_required'])
  result=a.restore(self.root,path,uuid.uuid4());target=Path(result['location'])
  self.assertEqual((target/'data/workbench/migration-original.zip').read_bytes(),raw)
  with closing(sqlite3.connect(target/'data/knowledge/library.sqlite3')) as c:self.assertEqual(c.execute('SELECT id,status,cancel_requested FROM jobs').fetchone(),('old','completed',0))
  with closing(sqlite3.connect(self.root/'data/knowledge/library.sqlite3')) as c:self.assertNotIn('cancel_requested',[r[1] for r in c.execute('PRAGMA table_info(jobs)')])
  self.assertEqual(path.read_bytes(),raw);self.assertIn('MODEL_CALLS_ENABLED=false',(target/'.env').read_text())
 def test_local_upgrade_uses_same_policy_and_saves_database_before_migration(self):
  self.old_archive();builder=a.distribution(self.root);package=builder.build(self.root,self.root/'package',kind='source');(self.root/'package.json').write_text(json.dumps({'version':'0.2.0'}));target=self.root.parent/'upgraded'
  plan=builder.upgrade(self.root,package,target);self.assertTrue(plan['compatibility']['migration_required']);self.assertFalse(target.exists())
  result=builder.upgrade(self.root,package,target,True);self.assertTrue(result['applied'])
  with closing(sqlite3.connect(target/'data/upgrade-pre-migration/library.sqlite3')) as c:self.assertNotIn('cancel_requested',[r[1] for r in c.execute('PRAGMA table_info(jobs)')]);self.assertEqual(c.execute('SELECT id FROM jobs').fetchone()[0],'old')
  with closing(sqlite3.connect(target/'data/knowledge/library.sqlite3')) as c:self.assertEqual(c.execute('SELECT cancel_requested FROM jobs').fetchone()[0],0)
  with closing(sqlite3.connect(self.root/'data/knowledge/library.sqlite3')) as c:self.assertNotIn('cancel_requested',[r[1] for r in c.execute('PRAGMA table_info(jobs)')])
 def test_local_upgrade_unknown_version_refuses_before_copy(self):
  builder=a.distribution(self.root);package=builder.build(self.root,self.root/'package',kind='source');(self.root/'package.json').write_text(json.dumps({'version':'0.1.0'}));target=self.root.parent/'unknown'
  with self.assertRaises(builder.DistributionError):builder.upgrade(self.root,package,target,True)
  self.assertFalse(target.exists())
 def test_unknown_or_downgrade_needs_decision(self):
  for source,target in [('0.3.0','0.2.0'),('0.1.0','0.3.0'),('9.0.0','0.3.0')]:
   result=a.compatibility(source,target);self.assertFalse(result['compatible']);self.assertTrue(result['requires_user_decision'])
 def test_cancelled_restore_removes_stage_and_preserves_source(self):
  path=self.old_archive();before=(self.root/'.env').read_bytes();stop=[False];token=uuid.uuid4()
  def progress(phase,done,total):
   if phase=='保留迁移前备份':stop[0]=True
  with self.assertRaises(a.OperationCancelled):a.restore(self.root,path,token,progress=progress,cancelled=lambda:stop[0])
  self.assertFalse((a.restore_home(self.root)/str(token)).exists());self.assertEqual(list(a.restore_home(self.root).glob('.restore-*')),[]);self.assertEqual((self.root/'.env').read_bytes(),before)
 def test_cancelled_backup_does_not_leave_downloadable_partial(self):
  path=self.root/'partial.zip';stop=[False]
  def progress(phase,done,total):stop[0]=True
  with self.assertRaises(a.OperationCancelled):a.create(self.root,path,progress=progress,cancelled=lambda:stop[0])
  self.assertFalse(path.exists())

class JobApiTests(Fixture):
 def test_async_backup_and_restore_without_blocking_progress(self):
  from app import workbench
  with self.client() as client:
   response=client.post('/api/v1/workbench/jobs',json={'kind':'backup'});self.assertEqual(response.status_code,200);identifier=response.json()['id']
   def wait(identifier):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
     value=client.get('/api/v1/workbench/jobs/'+identifier).json()
     if value['status'] in jobs.TERMINAL:return value
     time.sleep(.01)
    self.fail('job timeout')
   state=wait(identifier);self.assertEqual(state['status'],'completed',state['error'])
   response=client.get('/api/v1/workbench/jobs/'+identifier+'/download');self.assertEqual(response.status_code,200)
   token=str(uuid.uuid4());path=self.root/'data/workbench/incoming'/(token+'.zip');path.parent.mkdir(parents=True);path.write_bytes(response.content)
   response=client.post('/api/v1/workbench/jobs',json={'kind':'restore','token':token,'confirmed':True,'expected_sha256':a.file_sha(path)});self.assertEqual(response.status_code,200)
   state=wait(response.json()['id']);self.assertEqual(state['status'],'completed',state['error']);self.assertTrue(Path(state['result']['location']).is_dir())
   self.assertEqual(client.delete('/api/v1/workbench/jobs/'+identifier+'/artifact').status_code,200);self.assertFalse(jobs.read(self.root,identifier)['artifact_available']);self.assertTrue((self.root/'data/knowledge/raw.txt').exists())
 def test_busy_response_has_browser_cors_and_progress_remains_readable(self):
  from app.main import app
  from fastapi.testclient import TestClient
  client=TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',50000));owner='synthetic-cors';self.assertTrue(jobs.gate.reserve(owner))
  try:
   response=client.get('/api/v1/workbench/backups/previews',headers={'Origin':'http://127.0.0.1:5173'});self.assertEqual(response.status_code,503);self.assertEqual(response.headers['access-control-allow-origin'],'http://127.0.0.1:5173')
   response=client.get('/api/v1/workbench/jobs',headers={'Origin':'http://127.0.0.1:5173'});self.assertEqual(response.status_code,200)
  finally:jobs.gate.release(owner);client.close()

class JobTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
 def wait(self,identifier):
  end=time.monotonic()+5
  while time.monotonic()<end:
   value=jobs.read(self.root,identifier)
   if value['status'] in jobs.TERMINAL:return value
   time.sleep(.01)
  self.fail('worker did not finish')
 def test_cancel_running_releases_gate_and_keeps_retry_metadata(self):
  started=threading.Event()
  def operation(identifier,progress,cancelled):
   started.set()
   while True:a.checkpoint(progress,cancelled,'synthetic',1,2);time.sleep(.01)
  value=jobs.start(self.root,'backup',{'kind':'backup'},operation);self.assertTrue(started.wait(3));self.assertFalse(jobs.gate.enter());jobs.cancel(self.root,value['id']);end=self.wait(value['id'])
  self.assertEqual(end['status'],'cancelled');self.assertTrue(end['can_restart']);self.assertEqual(end['options'],{'kind':'backup'});self.assertTrue(jobs.gate.enter());jobs.gate.leave()
 def test_restart_marks_stale_task_interrupted_without_executing(self):
  identifier=str(uuid.uuid4())
  with closing(jobs.connect(self.root)) as c:c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,'backup','running','{}',None,'','copy',1,9,time.time(),time.time(),None,0));c.commit()
  jobs.recover(self.root);value=jobs.read(self.root,identifier);self.assertEqual(value['status'],'interrupted');self.assertTrue(value['can_restart']);self.assertFalse(value['can_cancel'])
 def test_failure_has_safe_message_and_explicit_restart(self):
  def operation(*args):raise RuntimeError('synthetic-private-log-secret')
  end=self.wait(jobs.start(self.root,'backup',{},operation)['id']);self.assertEqual(end['status'],'failed');self.assertNotIn('private-log',end['error']);self.assertTrue(end['can_restart'])
 def test_commit_phase_cannot_be_cancelled(self):
  identifier=str(uuid.uuid4())
  with closing(jobs.connect(self.root)) as c:c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,'restore','running','{}',None,'','提交独立副本',1,1,time.time(),time.time(),None,0));c.commit()
  with self.assertRaises(a.ArchiveError):jobs.cancel(self.root,identifier)

class RegressionTests(unittest.TestCase):
 def test_import_closure_handles_deleted_file_and_cycles(self):
  before={'src/App.tsx':"import './page'",'src/page.tsx':"import './part'",'src/part.tsx':"import './page'",'src/unused.ts':'1'};after={**before,'src/part.tsx':'changed'}
  result=regression.analyze(before,after,{'/':['src/App.tsx'],'/other':['src/unused.ts']});self.assertEqual(result['affected_files'],['src/App.tsx','src/page.tsx','src/part.tsx']);self.assertTrue(result['pages'][0]['affected']);self.assertFalse(result['pages'][1]['affected'])
  after.pop('src/part.tsx');self.assertIn('src/App.tsx',regression.analyze(before,after)['affected_files'])
 def test_compare_never_counts_deleted_or_skipped_tests_as_fixes(self):
  old={'build':'passed','tests':{'reg':'passed','old':'failed','fixed':'failed','deleted':'failed','skip':'failed'}};new={'build':'failed','tests':{'reg':'failed','old':'failed','fixed':'passed','new':'failed','skip':'skipped'}}
  result=regression.compare(old,new);self.assertEqual(result['regressions'],['reg']);self.assertEqual(result['existing_failures'],['old']);self.assertEqual(result['resolved'],['fixed']);self.assertEqual(result['not_retested'],['deleted']);self.assertEqual(result['baseline_not_covered'],['new']);self.assertTrue(result['build_regression'])
 def test_playwright_report_uses_stable_identity_and_failure_status(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'report.json';path.write_text(json.dumps({'suites':[{'file':'tests/a.spec.ts','specs':[{'title':'click','tests':[{'results':[{'status':'failed'},{'status':'passed'}]}]}]}]}));self.assertEqual(regression.test_results(path),{'tests/a.spec.ts/click':'failed'})

class DocumentCancellationTests(unittest.TestCase):
 def test_cancellation_prevents_parser_spawn_and_frees_gate(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(document_runtime,'DATA',Path(temp)),patch.object(document_runtime,'_run') as spawn:
   with document_runtime.cancellation(lambda:True),self.assertRaises(document_runtime.DocumentRuntimeError):document_runtime.extract_document(b'synthetic','.txt')
   spawn.assert_not_called();self.assertTrue(document_runtime._thread_gate.acquire(blocking=False));document_runtime._thread_gate.release()
 def test_cancel_reindex_keeps_original_chunks_and_allows_retry(self):
  from fastapi import BackgroundTasks
  with tempfile.TemporaryDirectory() as temp,patch.object(library,'DATA',Path(temp)):
   cid=str(uuid.uuid4());did=str(uuid.uuid4());jid=str(uuid.uuid4());now=library.now()
   with closing(library.connect()) as c:
    c.execute('INSERT INTO collections VALUES (?,?,?,?)',(cid,'synthetic','',now));c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(did,cid,'test.txt',4,'hash','parsing',20,'','[]',1,1,jid,now,now));c.execute('INSERT INTO jobs(id,document_id,status,progress,error) VALUES (?,?,?,?,?)',(jid,did,'parsing',20,''));c.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)',('chunk',did,cid,0,1,1,1,'old content',0,11));c.commit()
   raw=library.raw_path(did);raw.write_bytes(b'keep');library.cancel_job(uuid.UUID(jid))
   with closing(library.connect()) as c:self.assertEqual(c.execute('SELECT status FROM documents').fetchone()[0],'ready');self.assertEqual(c.execute('SELECT content FROM chunks').fetchone()[0],'old content')
   self.assertEqual(raw.read_bytes(),b'keep');self.assertTrue(library.job_cancelled(jid));self.assertEqual(library.retry_document(uuid.UUID(did),BackgroundTasks())['status'],'queued')
if __name__=='__main__':unittest.main()
