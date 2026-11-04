"""Bounded closing checks. All data synthetic; all provider transports forbidden or in memory."""
import errno,json,os,shutil,sqlite3,subprocess,sys,tempfile,threading,time,unittest,uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import acceptance as a,runner,project,store,tools,dify,graph,workbench_archive as archive,workbench_jobs as jobs,maintenance_errors,document_runtime
from app.runtime_limits import worker_slot
from test_workbench import Fixture

class AcceptanceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.id=str(uuid.uuid4())
  for item in [patch.object(runner,'ROOT',self.root),patch.object(runner,'GENERATED_ROOT',self.root/'generated-projects'),patch.object(runner,'OUTPUT_ROOT',self.root/'outputs'),patch.object(store,'DATA_DIR',self.root/'data'),patch.object(store,'RUNS_DB',self.root/'data/runs.sqlite3')]:item.start();self.addCleanup(item.stop)
  (self.root/'benchmarks').mkdir();source=Path(__file__).resolve().parents[1]/'benchmarks/todo-v1.spec.ts';shutil.copyfile(source,self.root/'benchmarks/todo-v1.spec.ts')
  self.state={'run_id':self.id,'status':'completed','acceptance_profile':'generated','acceptance_criteria':['能够新增任务','刷新后任务仍在'],'executor_tasks':[{'id':'task-1','acceptance_indices':[0]}]};store.create_run(self.id,'synthetic',self.state,'offline');runner.initialize_workspace(self.id)
  files={name:runner.BASE_FILES[name] for name in project.REQUIRED_FILES};files['tests/e2e.spec.ts']="import {test} from '@playwright/test';test('self',()=>{})";project.import_sources(self.id,files)
 def receipt(self,kind,passed=True,**extra):
  data={'digest':project.source_digest(project.sources(self.id)),'passed':passed,'profile':self.state['acceptance_profile'],'suite_sha256':a.suite_hash(self.state['acceptance_profile']),'health_passed':True,'health_sha256':a.health_hash(),**extra}
  with closing(tools.connect()) as c:c.execute('INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)',(self.id,str(uuid.uuid4()),'quality',kind,'fixture','{}','completed',json.dumps({'ok':True,'data':data}),'offline',str(time.time())));c.commit()
 def proof(self):self.receipt('project.build');self.receipt('project.test')
 def save(self,entries=None,**changes):
  value=a.matrix(self.id);payload={'revision':value['revision'],'expected_basis_hash':value['basis_hash'],'reviews':entries if entries is not None else [{'criterion':i,'method':'manual','result':'passed','note':'已在合成预览实际核对操作'} for i in range(2)],**changes};return a.save_reviews(uuid.UUID(self.id),a.SaveReviews.model_validate(payload))
 def test_self_tests_and_task_links_never_confirm_requirements(self):
  self.proof();value=a.matrix(self.id);self.assertEqual(value['counts']['passed'],0);self.assertEqual(value['review_status'],'pending');self.assertEqual(value['technical']['test_kind'],'generated');self.assertEqual(value['criteria'][0]['candidate_task_ids'],['task-1'])
 def test_independent_suite_never_implicitly_covers_all_criteria(self):
  self.state['acceptance_profile']='todo-v1';store.update_run(self.id,self.state);self.proof();value=a.matrix(self.id);self.assertEqual(value['technical']['test_kind'],'independent_suite');self.assertEqual(value['counts']['unverified'],2)
  result=self.save([{'criterion':0,'method':'independent_suite','result':'passed','note':'新增任务用例对应此条新增要求'}]);self.assertEqual(result['counts']['passed'],1);self.assertEqual(result['review_status'],'pending')
 def test_manual_confirmation_is_source_bound_and_audited(self):
  self.proof();value=self.save();self.assertEqual(value['review_status'],'confirmed')
  files=project.sources(self.id);files['src/style.css']+='\nbody{color:black}';project.import_sources(self.id,files);changed=a.matrix(self.id);self.assertEqual(changed['counts']['passed'],0);self.assertTrue(all(r['status']=='stale' for r in changed['criteria']))
  with store.db_connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM acceptance_review_events').fetchone()[0],1)
 def test_changed_requirements_or_suite_invalidates_saved_reviews(self):
  self.state['acceptance_profile']='todo-v1';store.update_run(self.id,self.state);self.proof();self.save();suite=self.root/'benchmarks/todo-v1.spec.ts';suite.write_text(suite.read_text()+'\n// changed');self.assertEqual(a.matrix(self.id)['counts']['passed'],0)
  self.state['acceptance_criteria'].reverse();store.update_run(self.id,self.state);self.assertEqual(a.matrix(self.id)['counts']['passed'],0)
 def test_stale_view_and_concurrent_edit_are_rejected(self):
  self.proof();before=a.matrix(self.id);self.save()
  with self.assertRaises(HTTPException) as caught:self.save(revision=before['revision'])
  self.assertEqual(caught.exception.status_code,409)
  with self.assertRaises(HTTPException):self.save(expected_basis_hash='0'*64)
 def test_fake_independent_claim_duplicate_or_unknown_criterion_is_rejected(self):
  self.proof()
  with self.assertRaises(HTTPException):self.save([{'criterion':0,'method':'independent_suite','result':'passed','note':'伪造独立证据应该被拒绝'}])
  for indices in [[0,0],[2]]:
   with self.assertRaises(HTTPException):self.save([{'criterion':i,'method':'manual','result':'passed','note':'测试非法序号不会写入'} for i in indices])
 def test_manual_pass_without_current_technical_proof_stays_pending(self):self.assertEqual(self.save()['review_status'],'pending')
 def test_latest_failure_supersedes_older_success_and_cache(self):
  self.proof();self.receipt('project.test',False);self.assertFalse(a.matrix(self.id)['technical']['test_passed']);self.assertIsNone(tools.successful_validation(self.id,'project.test',project.source_digest(project.sources(self.id))))
 def test_legacy_or_stale_independent_receipt_cannot_claim_verification(self):
  self.state['acceptance_profile']='todo-v1';store.update_run(self.id,self.state);self.receipt('project.build');self.receipt('project.test',suite_sha256=None,health_sha256=None);value=a.matrix(self.id);self.assertFalse(value['technical']['test_passed']);self.assertFalse(value['technical']['health_passed'])
 def test_active_run_cannot_be_manually_confirmed(self):
  self.state['status']='running';store.update_run(self.id,self.state)
  with self.assertRaises(HTTPException):self.save()
 def test_agent_tool_registry_has_no_human_review_tool(self):self.assertFalse(any('acceptance' in name for name in tools.TOOLS))

class DifyConfigurationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  for item in [patch.object(dify,'DATA',self.root),patch.object(dify,'credentials',return_value='synthetic-key'),patch.object(dify.request,'build_opener',side_effect=AssertionError('Remote transport forbidden'))]:item.start();self.addCleanup(item.stop)
 def save(self,**overrides):return dify.confirm_free_policy(dify.FreePolicyConfirmation.model_validate({'expected_revision':dify.policy_revision(),'observed_used_credits':35,'local_total_credit_ceiling':60,'free_model_confirmed':True,'no_paid_fallback':True,**overrides}))
 def test_clean_install_can_confirm_without_any_network_request(self):
  self.assertEqual(dify.status()['policy_revision'],'missing');value=self.save();self.assertEqual(value['network_requests'],0);self.assertTrue(value['status']['free_confirmed_at']);self.assertEqual(dify.policy()['observed_used_credits'],35)
 def test_reconfirmation_does_not_reset_ledger_or_reduce_observation(self):
  self.save()
  with closing(dify.connect()) as c:c.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),time.time(),'POST','/datasets/x/retrieve','uncertain','retrieve',5));c.commit()
  self.save(observed_used_credits=0);self.assertEqual(dify.policy()['observed_used_credits'],35);self.assertEqual(dify.status()['reserved_credits'],5);self.assertEqual(dify.status()['local_remaining_credits'],20)
 def test_resume_requires_explicit_check_and_preserves_pause_until_clicked(self):
  self.save()
  with closing(dify.connect()) as c:c.execute('INSERT INTO controls VALUES (1,?)',('synthetic rejection',));c.commit()
  self.save();self.assertTrue(dify.status()['paused'])
  result=dify.resume_after_console_check(dify.ResumeConfirmation(expected_revision=dify.policy_revision(),console_checked=True));self.assertIsNone(result['status']['paused']);self.assertFalse(result['automatic_retry']);self.assertEqual(result['network_requests'],0)
 def test_exhausted_budget_and_changed_key_block_resume(self):
  self.save(observed_used_credits=60)
  with self.assertRaises(dify.DifyError):dify.resume_after_console_check(dify.ResumeConfirmation(expected_revision=dify.policy_revision(),console_checked=True))
  with patch.object(dify,'credentials',return_value='different-key'),self.assertRaises(dify.DifyError):dify.policy()
 def test_policy_stale_revision_and_false_confirmation_are_rejected(self):
  self.save()
  with self.assertRaises(HTTPException):self.save(expected_revision='missing')
  from pydantic import ValidationError
  with self.assertRaises(ValidationError):self.save(free_model_confirmed=False)
 def test_pending_remote_change_survives_local_resume(self):
  self.save()
  with closing(dify.connect()) as c:c.execute('INSERT INTO controls VALUES (1,?)',('synthetic rejection',));c.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),time.time(),'POST','/datasets','uncertain','metadata',0));c.commit()
  dify.resume_after_console_check(dify.ResumeConfirmation(expected_revision=dify.policy_revision(),console_checked=True))
  with self.assertRaises(dify.DifyError):dify.create(dify.Create(name='synthetic'))

class StabilityTests(Fixture):
 def test_disk_full_mid_backup_drops_partial_and_keeps_original(self):
  before=(self.root/'data/knowledge/raw.txt').read_bytes();target=self.root/'partial.zip'
  with patch.object(archive.zipfile.ZipFile,'writestr',side_effect=OSError(errno.ENOSPC,'PRIVATE PATH')):
   with self.assertRaises(OSError):archive.create(self.root,target)
  self.assertFalse(target.exists());self.assertEqual((self.root/'data/knowledge/raw.txt').read_bytes(),before)
 def test_entry_and_size_limits_refuse_without_export(self):
  for key,value in [('MAX_ENTRIES',1),('LIMIT',64)]:
   with patch.object(archive,key,value),self.assertRaises(archive.ArchiveError):self.create()
  self.assertFalse((self.root/'backup.zip').exists())
 def test_runtime_slot_blocks_other_thread_and_releases_after_failure(self):
  errors=[]
  def contender():
   try:
    with worker_slot(self.root):errors.append('unexpected acquire')
   except runner.SandboxError:errors.append('busy')
  with worker_slot(self.root):
   thread=threading.Thread(target=contender);thread.start();thread.join(3);self.assertEqual(errors,['busy'])
  with self.assertRaises(ValueError):
   with worker_slot(self.root):raise ValueError('synthetic')
  with worker_slot(self.root):pass
 def test_busy_runtime_prevents_starting_phase(self):
  with worker_slot(self.root),patch.object(runner,'_run_phase') as execute:
   with self.assertRaises(runner.SandboxError):runner.run_phase(self.id,'build')
   execute.assert_not_called()
 def test_maintenance_queue_limit_and_restart_do_not_execute_jobs(self):
  class Deferred:
   def submit(self,*args):pass
  with patch.object(jobs,'_pool',Deferred()):
   for _ in range(4):jobs.start(self.root,'backup',{},lambda *a:None)
   with self.assertRaises(archive.ArchiveError):jobs.start(self.root,'backup',{},lambda *a:None)
   jobs.recover(self.root);self.assertTrue(all(j['status']=='interrupted' for j in jobs.recent(self.root)))
 def test_real_process_restart_keeps_partial_non_downloadable_and_unlocks_worker(self):
  helper="""
import sys,time
from pathlib import Path
from app import workbench_jobs as jobs
from app.runtime_limits import worker_slot
root=Path(sys.argv[1])
def operation(identifier,progress,cancelled):
 with worker_slot(root):
  artifacts=jobs.folder(root)/'artifacts';artifacts.mkdir(exist_ok=True)
  (artifacts/(identifier+'.zip')).write_bytes(b'synthetic incomplete archive')
  progress('synthetic copying',1,9)
  (root/'started.json').write_text(identifier)
  time.sleep(30)
jobs.start(root,'backup',{},operation)
while True:time.sleep(.1)
"""
  env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','LANG','LC_ALL'}};env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'apps/api');env['PYTHONUTF8']='1'
  child=subprocess.Popen([sys.executable,'-c',helper,str(self.root)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   deadline=time.monotonic()+10
   while not (self.root/'started.json').exists() and time.monotonic()<deadline:
    if child.poll() is not None:self.fail('synthetic maintenance child exited early')
    time.sleep(.05)
   self.assertTrue((self.root/'started.json').is_file());identifier=(self.root/'started.json').read_text()
   with self.assertRaises(runner.SandboxError):
    with worker_slot(self.root):pass
  finally:
   child.kill();child.wait(timeout=5)
  jobs.recover(self.root);state=jobs.read(self.root,identifier);self.assertEqual(state['status'],'interrupted');self.assertTrue(state['artifact_available']);self.assertTrue(state['can_restart'])
  from app import workbench
  with self.assertRaises(HTTPException):workbench.download_job(uuid.UUID(identifier))
  with worker_slot(self.root):pass
  self.assertTrue((self.root/'data/knowledge/raw.txt').is_file())
 def test_failure_codes_do_not_include_private_errors(self):
  for error,code in [(OSError(errno.ENOSPC,'PRIVATE PATH'),'disk_space'),(PermissionError('PRIVATE PATH'),'permission_denied'),(FileNotFoundError('PRIVATE PATH'),'runtime_unavailable'),(sqlite3.DatabaseError('PRIVATE SQL'),'database_invalid'),(subprocess.TimeoutExpired('PRIVATE COMMAND',3),'operation_timeout'),(RuntimeError('PRIVATE SECRET'),'unknown_failure')]:
   result=maintenance_errors.describe(error);self.assertEqual(result['code'],code);self.assertNotIn('PRIVATE',json.dumps(result));self.assertFalse(result['automatic_retry'])
 def test_diagnostics_accept_only_catalogued_failure_codes(self):
  from app import workbench
  identifier=str(uuid.uuid4())
  with closing(jobs.connect(self.root)) as c:c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,'backup','failed','{}',json.dumps({'failure':{'code':'PRIVATE SECRET'}}),'PRIVATE LOG','failed',0,1,time.time(),None,time.time(),0));c.commit()
  result=workbench.diagnostic_report();self.assertEqual(result['maintenance_failures'][0]['error_code'],'unknown_failure');self.assertNotIn('PRIVATE',json.dumps(result))

class ParserLifecycleTests(unittest.TestCase):
 def run_child(self,cancel,memory=None):
  original=subprocess.Popen;children=[];start=time.monotonic()
  def synthetic(args,**kwargs):
   child=original([sys.executable,'-c','import time;time.sleep(30)'],**kwargs);children.append(child);return child
  with tempfile.TemporaryDirectory() as temp,patch.object(document_runtime,'DATA',Path(temp)),patch.object(document_runtime.subprocess,'Popen',side_effect=synthetic):
   with patch.object(document_runtime,'MEMORY_BYTES',memory or document_runtime.MEMORY_BYTES),document_runtime.cancellation(lambda:cancel and time.monotonic()-start>.15),self.assertRaises(document_runtime.DocumentRuntimeError):document_runtime.extract_document(b'synthetic','.pdf')
  self.assertTrue(children);self.assertIsNotNone(children[0].poll());self.assertLess(time.monotonic()-start,5)
 def test_cancel_running_parser_terminates_child(self):self.run_child(True)
 def test_memory_watchdog_terminates_child_without_large_allocation(self):self.run_child(False,1)

class DiagnosisTests(unittest.TestCase):
 def test_fullstack_scope_is_not_treated_as_frontend_only(self):
  text=graph.diagnosis_prompt('fullstack_crud');self.assertIn('FastAPI/SQLite',text);self.assertNotIn('超出React/localStorage范围选择stop',text);self.assertIn('不允许生成任意 Python/SQL',text)
 def test_frontend_scope_stays_localstorage(self):
  text=graph.diagnosis_prompt('frontend');self.assertIn('localStorage',text);self.assertIn('不增加业务后端',text)
if __name__=='__main__':unittest.main()
