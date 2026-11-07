"""Offline regressions for audit findings. Synthetic storage and mocked provider only."""
import json,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from pydantic import BaseModel
from app import context,model,store,designs,tools,runner,project,acceptance,page_health,runtime_identity
import test_closeout
CURRENT_IDENTITY=runtime_identity.current

class ContextTests(unittest.TestCase):
 def test_long_failure_tail_and_read_offsets_survive_truncation(self):
  value=context.result_excerpt({'ok':True,'data':{'output':'x'*15000+'FAILURE_AT_END','passed':False}},1000)
  parsed=json.loads(value);self.assertTrue(parsed['data']['output'].endswith('FAILURE_AT_END'));self.assertLessEqual(len(value),1000)
  value=context.result_excerpt({'ok':True,'data':{'content':'a\n'*10000,'offset':12000,'sha256':'a'*64,'total_characters':50000}},1000)
  parsed=json.loads(value);self.assertEqual(parsed['data']['offset'],12000);self.assertEqual(parsed['data']['sha256'],'a'*64);self.assertGreater(parsed['data']['context_excerpt']['omitted_characters'],0)
 def test_latest_failure_survives_large_old_file_reads(self):
  history=[{'tool_name':'project.read_file','task_id':'t','result':{'content':'x'*20000}} for _ in range(4)]
  history.append({'tool_name':'project.test','task_id':'t','result':{'passed':False,'output':'NEW_FAILURE'}})
  value=context.recent_results(history)
  self.assertIn('NEW_FAILURE',value[-1]['result']);self.assertLessEqual(sum(len(v['result']) for v in value),30000)
  self.assertEqual(value[0]['result'],'');self.assertGreater(value[0]['omitted_characters'],0)
 def test_small_histories_are_lossless_and_chronological(self):
  history=[{'tool_name':'read','task_id':'t','result':{'value':v}} for v in range(5)]
  self.assertEqual([json.loads(x['result'])['value'] for x in context.recent_results(history)],list(range(5)))
 def test_index_prioritizes_relevant_files_without_executing_them(self):
  files={f'src/file{i}.ts':'export function unrelated(){}' for i in range(100)}
  files['src/Search.tsx']='import {useState} from "react"; export function Search(){throw Error("must never execute")}'
  value=context.source_index(files,{'title':'Search'},budget=300)
  self.assertEqual(value['files'][0]['path'],'src/Search.tsx');self.assertGreater(value['omitted_files'],0)
  self.assertLessEqual(sum(len(json.dumps(x,ensure_ascii=False)) for x in value['files']),300)
 def test_task_memory_keeps_bounded_completed_work_only(self):
  tasks=[{'id':str(i),'status':'completed','completion_summary':'a'*600,'changed_paths':['src/a.ts']*30} for i in range(20)]
  value=context.task_memory(tasks+[{'id':'pending','status':'pending'}])
  self.assertEqual(value['completed_count'],20);self.assertLessEqual(len(value['recent_completed']),8);self.assertLessEqual(sum(len(json.dumps(x,ensure_ascii=False)) for x in value['recent_completed']),2000)
  self.assertEqual(value['recent_completed'][-1]['id'],'19');self.assertEqual(len(value['recent_completed'][0]['summary']),300)

class UsageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
  settings={'enabled':True,'provider':'deepseek','model':'deepseek-flash','budget_cny':99,'api_key':'synthetic','base_url':'unused'}
  self.client=Mock();self.client.chat.completions.create.return_value=SimpleNamespace(usage=None,choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{"text":"synthetic"}'))])
  for p in [patch.object(model,'DATA_DIR',root),patch.object(model,'USAGE_DB',root/'usage.sqlite3'),patch.object(model,'_get_settings',return_value=settings),patch.object(model,'_client',return_value=self.client),patch.object(store,'get_run',return_value={'run_budget_cny':5}),patch.object(designs,'get_design',return_value=None)]:p.start();self.addCleanup(p.stop)
 def call(self,stage='test'):
  class Result(BaseModel):text:str
  return model.generate_json('synthetic',stage,'offline','x'*60000,Result,max_tokens=2000)
 def test_missing_usage_keeps_reservation_on_receipt_replay(self):
  value,metadata=self.call();self.assertEqual(metadata['usage_status'],'missing_reservation_held');self.assertIsNone(metadata['input_tokens'])
  with model._db_connection() as conn:row=conn.execute('SELECT * FROM model_usage').fetchone()
  self.assertIsNone(row['actual_cny']);self.assertEqual(row['status'],'usage_missing');self.assertEqual(metadata['estimated_cost_cny'],row['reserve_cny'])
  self.assertEqual(self.call()[1]['estimated_cost_cny'],metadata['estimated_cost_cny']);self.assertEqual(self.client.chat.completions.create.call_count,1)
  self.assertEqual(model.budget_status('synthetic')['spent_cny_estimate'],row['reserve_cny'])
 def test_partial_or_invalid_usage_is_not_settled(self):
  for index,usage in enumerate([SimpleNamespace(prompt_tokens=10),SimpleNamespace(prompt_tokens=-1,completion_tokens=2),SimpleNamespace(prompt_tokens=True,completion_tokens=3)]):
   self.client.chat.completions.create.return_value.usage=usage
   self.assertEqual(self.call(str(index))[1]['usage_status'],'missing_reservation_held')
 def test_reported_usage_settles_normally(self):
  self.client.chat.completions.create.return_value.usage=SimpleNamespace(prompt_tokens=20000,completion_tokens=100)
  self.assertEqual(self.call()[1]['usage_status'],'reported')
  with model._db_connection() as conn:self.assertIsNotNone(conn.execute('SELECT actual_cny FROM model_usage').fetchone()[0])
 def test_invalid_response_without_usage_still_keeps_reserve(self):
  self.client.chat.completions.create.return_value.choices[0].message.content='not json'
  with self.assertRaises(model.ModelOutputInvalid):self.call()
  with model._db_connection() as conn:self.assertIsNone(conn.execute('SELECT actual_cny FROM model_usage').fetchone()[0])

class EvidenceTests(unittest.TestCase):
 setUp=test_closeout.AcceptanceTests.setUp
 receipt=test_closeout.AcceptanceTests.receipt
 proof=test_closeout.AcceptanceTests.proof
 save=test_closeout.AcceptanceTests.save
 def test_image_id_and_fixed_framework_content_change_fingerprint(self):
  def inspected(value):return SimpleNamespace(returncode=0,stdout='sha256:'+value*64)
  with patch.object(runner,'run_bounded',return_value=inspected('a')):
   first=CURRENT_IDENTITY(self.id)
  with patch.object(runner,'run_bounded',return_value=inspected('b')):
   self.assertNotEqual(first['digest'],CURRENT_IDENTITY(self.id)['digest'])
  with patch.object(runner,'run_bounded',return_value=inspected('a')),patch.object(project,'framework_hashes',return_value={'fixed':'changed'}):
   self.assertNotEqual(first['digest'],CURRENT_IDENTITY(self.id)['digest'])
  with patch.object(runner,'run_bounded',return_value=SimpleNamespace(returncode=1,stdout='')):
   self.assertIsNone(CURRENT_IDENTITY(self.id))
 def test_environment_change_invalidates_proof_and_manual_confirmation(self):
  self.proof();self.assertEqual(self.save()['review_status'],'confirmed')
  with patch.object(runtime_identity,'current',return_value={'digest':'different','image_id':'sha256:'+'b'*64}):
   value=acceptance.matrix(self.id);self.assertFalse(value['technical']['build_passed']);self.assertEqual(value['criteria'][0]['status'],'stale')
   self.assertIsNone(tools.successful_validation(self.id,'project.build',project.manifest(self.id)['digest']))
 def test_unavailable_environment_never_reuses_old_proof(self):
  self.proof()
  with patch.object(runtime_identity,'current',return_value=None):self.assertFalse(acceptance.matrix(self.id)['technical']['environment_verified'])
 def test_old_receipt_without_environment_is_invalid(self):
  self.receipt('project.build',environment_sha256=None)
  self.assertIsNone(tools.successful_validation(self.id,'project.build',project.manifest(self.id)['digest']))
 def test_receipt_replay_rejects_changed_environment(self):
  c=tools.ToolContext(self.id,'test','build-once')
  with patch.object(runner,'run_phase',return_value=runner.CommandResult(0,'offline',1)):
   self.assertTrue(tools.execute(c,'project.build',{})['ok'])
  with patch.object(runtime_identity,'current',return_value={'digest':'new','image_id':'sha256:'+'b'*64}),patch.object(runner,'run_phase') as run:
   result=tools.execute(c,'project.build',{});self.assertEqual(result['error']['code'],'stale_validation');run.assert_not_called()
 def test_fixed_route_plan_and_hash_follow_confirmed_pages(self):
  before=page_health.fingerprint(self.id)
  self.state['product_spec']={'page_details':[{'route':'/'},{'route':'/settings'},{'route':'/tasks/:id'},{'route':'https://external'},{'route':'/settings'}]};store.update_run(self.id,self.state)
  value=page_health.coverage(self.id);self.assertEqual(value['routes'],['/','/#/settings']);self.assertEqual(len(value['pending_routes']),2)
  self.assertNotEqual(page_health.fingerprint(self.id),before);self.assertNotIn('https://external',page_health.script(self.id))
 def test_environment_change_during_build_is_not_accepted(self):
  identity={'digest':'test-environment','image_id':'sha256:'+'a'*64}
  with patch.object(runtime_identity,'current',side_effect=[identity,identity,{'digest':'changed'}]),patch.object(runner,'run_phase',return_value=runner.CommandResult(0,'offline',1)) as run:
   with self.assertRaises(runner.SandboxError):tools._build(tools.ToolContext(self.id,'t','b'),tools.EmptyArgs())
   self.assertEqual(run.call_args.kwargs['image_id'],identity['image_id'])

if __name__=='__main__':unittest.main()
