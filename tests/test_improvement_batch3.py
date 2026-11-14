"""Third-batch contracts use synthetic storage, providers and source text only."""
import json,os,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from pydantic import BaseModel,ValidationError
from fastapi import HTTPException
from app import model,text_models,team,team_policy,store,designs,project,runner,tools,execution_replay as replay,review_context,workbench
import test_team,test_interactive_expansion,test_workbench

class Reply(BaseModel):text:str

class ProviderTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
  self.settings={'enabled':True,'provider':'deepseek','model':'deepseek-flash','budget_cny':99,'api_key':'synthetic-ds','base_url':'https://api.deepseek.com'}
  self.state={'run_budget_cny':5}
  self.client=Mock();self.client.chat.completions.create.return_value=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000,completion_tokens=100),choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{"text":"synthetic"}'))])
  for p in [patch.dict(os.environ,{'QWEN_TEXT_API_KEY':'synthetic-qwen','QWEN_TEXT_CALLS_ENABLED':'true','QWEN_TEXT_USE_VISION_KEY':'false','QWEN_TEXT_BASE_URL':text_models.QWEN_BASE}),patch.object(model,'DATA_DIR',root),patch.object(model,'USAGE_DB',root/'usage.sqlite3'),patch.object(model,'_get_settings',return_value=self.settings),patch.object(model,'OpenAI',return_value=self.client),patch.object(store,'get_run',side_effect=lambda _:self.state),patch.object(designs,'get_design',return_value=None)]:p.start();self.addCleanup(p.stop)
 def call(self,stage='test',selected='qwen3.8-flash'):
  return model.generate_json('synthetic',stage,'只输出JSON','合成输入',Reply,max_tokens=1000,model_name=selected)
 def test_qwen_protocol_rates_and_cached_receipt(self):
  value,usage=self.call();kwargs=self.client.chat.completions.create.call_args.kwargs
  self.assertEqual(kwargs['extra_body'],{'enable_thinking':False});self.assertEqual(kwargs['response_format'],{'type':'json_object'})
  self.assertEqual(usage['provider'],'aliyun_text');self.assertAlmostEqual(usage['estimated_cost_cny'],.00107)
  self.assertTrue(self.call()[1]['replayed']);self.client.chat.completions.create.assert_called_once()
  model.OpenAI.assert_called_once_with(api_key='synthetic-qwen',base_url=text_models.QWEN_BASE,timeout=45,max_retries=0)
 def test_independent_and_global_gates_block_before_client_or_charge(self):
  with patch.dict(os.environ,{'QWEN_TEXT_CALLS_ENABLED':'false'}):
   with self.assertRaises(model.ModelNotConfigured):self.call()
  self.settings['enabled']=False
  with self.assertRaises(model.ModelNotConfigured):self.call()
  model.OpenAI.assert_not_called();self.assertEqual(model.budget_status()['calls'],0)
 def test_existing_vision_key_requires_explicit_reuse(self):
  with patch.dict(os.environ,{'QWEN_TEXT_API_KEY':'','VISION_API_KEY':'synthetic-vision'}):
   self.assertFalse(text_models.route('qwen3.8-flash',self.settings)['api_key'])
   with patch.dict(os.environ,{'QWEN_TEXT_USE_VISION_KEY':'true'}):self.assertEqual(text_models.route('qwen3.8-flash',self.settings)['api_key'],'synthetic-vision')
 def test_non_beijing_or_custom_endpoints_rejected_before_credentials_sent(self):
  for endpoint in ['http://127.0.0.1/v1','https://evil.example/v1','https://dashscope-intl.aliyuncs.com/compatible-mode/v1',text_models.QWEN_BASE+'?leak=x']:
   with patch.dict(os.environ,{'QWEN_TEXT_BASE_URL':endpoint}):
    with self.assertRaises(model.ModelNotConfigured):self.call()
  model.OpenAI.assert_not_called()
 def test_pro_reserve_uses_published_peak_input_price(self):
  self.assertEqual(text_models.MODELS['deepseek-v4-pro']['rates_cny'],(1.32*8,3.96*8))
 def test_deepseek_retains_original_protocol_and_rates(self):
  _,usage=self.call(selected='deepseek-flash');self.assertEqual(self.client.chat.completions.create.call_args.kwargs['extra_body'],{'thinking':{'type':'disabled'}})
  self.assertAlmostEqual(usage['estimated_cost_cny'],.00336)
 def test_missing_usage_keeps_reserve_on_replay(self):
  self.client.chat.completions.create.return_value.usage=None
  first=self.call()[1];self.assertEqual(first['usage_status'],'missing_reservation_held')
  self.assertEqual(self.call()[1]['estimated_cost_cny'],first['estimated_cost_cny']);self.assertGreaterEqual(first['estimated_cost_cny'],.25)
 def test_uncertain_failure_has_no_provider_fallback_or_retry(self):
  self.client.chat.completions.create.side_effect=TimeoutError('synthetic')
  with self.assertRaises(model.ModelCallFailed):self.call()
  self.client.chat.completions.create.assert_called_once()
  with model._db_connection() as conn:self.assertEqual(conn.execute('SELECT status FROM model_usage').fetchone()[0],'uncertain')
 def test_providers_share_one_task_budget(self):
  self.state['run_budget_cny']=.25;self.client.chat.completions.create.return_value.usage=None
  self.call(selected='deepseek-flash')
  with self.assertRaises(model.BudgetExceeded):self.call(stage='qwen')
  self.client.chat.completions.create.assert_called_once()
 def test_endpoint_change_cannot_replay_wrong_receipt(self):
  self.call()
  with patch.dict(os.environ,{'QWEN_TEXT_BASE_URL':'https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'}):
   with self.assertRaises(model.ModelCallFailed):model.cached_json('synthetic','test','只输出JSON','合成输入',Reply,1000,'qwen3.8-flash')
  self.client.chat.completions.create.assert_called_once()

class RoleTests(unittest.TestCase):
 setUp=test_team.TeamTests.setUp
 invoke=test_team.TeamTests.invoke
 def test_role_route_is_recorded_and_replayed_without_new_call(self):
  self.state['role_models']=text_models.frozen_roles('deepseek-flash',{'reviewer':'qwen3.8-flash'})
  self.invoke('reviewer');self.assertEqual(self.provider.call_args.kwargs['model_name'],'qwen3.8-flash')
  self.invoke('reviewer');self.provider.assert_called_once()
  self.state['role_models']['reviewer']='deepseek-v4-pro'
  with self.assertRaises(runner.SandboxError):self.invoke('reviewer')
 def test_reviewer_reserve_follows_reviewer_not_developer(self):
  state={'model_name':'deepseek-flash','role_models':{'reviewer':'deepseek-v4-pro'},'run_budget_cny':3}
  self.assertEqual(team_policy.reserve(state)['estimated_cny'],1.05)
 def test_config_rejects_unknown_role_and_model(self):
  for mapping in [{'reviewer':'unknown'},{'superuser':'qwen3.8-flash'}]:
   with self.assertRaises(ValidationError):designs.DesignRequest(request='这是十个字以上的合成测试需求',role_models=mapping)
 def test_old_state_still_uses_task_model(self):
  self.state['model_name']='deepseek-v4-pro';self.invoke('planner')
  self.assertEqual(self.provider.call_args.kwargs['model_name'],'deepseek-v4-pro')

class ReviewTests(unittest.TestCase):
 def test_first_review_hides_plan_claims_and_keeps_requirements(self):
  state={'model_name':'deepseek-flash','role_models':{'reviewer':'qwen3.8-flash'},'acceptance_criteria':['Search可以检索'],'repair_round':0}
  common={'manifest':{'files':{}},'plan_coverage':['developer says done'],'previous_issues':[{'status':'gap'}],'technical':{'build':False}}
  result=review_context.augment(common,state,{'src/Search.tsx':'export const Search=()=>null'},[])
  self.assertNotIn('plan_coverage',result);self.assertNotIn('previous_issues',result)
  self.assertEqual(result['review_packet']['criteria'][0]['criterion'],'Search可以检索');self.assertFalse(result['technical']['build']);self.assertIn('plan_coverage',common)
  state['repair_round']=1;self.assertIn('previous_issues',review_context.augment(common,state,{},[]))
 def test_stale_source_read_is_not_presented_as_current(self):
  common={'manifest':{'files':{'src/A.ts':{'sha256':'new'}}}}
  history=[{'tool_name':'project.read_file','result':{'ok':True,'data':{'path':'src/A.ts','sha256':'old','content':'x'}}}]
  result=review_context.augment(common,{'acceptance_criteria':[]},{},history)
  self.assertEqual(result['review_packet']['recent_current_source_reads'],[])

class SetupTests(test_workbench.Fixture):
 def test_qwen_config_separates_permission_and_preserves_keys(self):
  with (self.root/'.env').open('a') as f:f.write('VISION_API_KEY=synthetic-vision\nVISION_CALLS_ENABLED=false\n')
  workbench.save_setup(workbench.SetupConfig(provider='qwen_text',reuse_vision_key=True,enabled=False))
  text=(self.root/'.env').read_text();self.assertIn(self.secret,text);self.assertIn('QWEN_TEXT_CALLS_ENABLED=false',text);self.assertIn('VISION_CALLS_ENABLED=false',text)
  with patch.object(workbench,'local_command',return_value=False):status=workbench.setup_status()
  self.assertNotIn('synthetic-vision',json.dumps(status));self.assertTrue(status['qwen_text']['reuse_vision_key'])
 def test_qwen_save_rejects_endpoint_and_missing_key(self):
  before=(self.root/'.env').read_bytes()
  for payload in [dict(provider='qwen_text',base_url='https://evil.example'),dict(provider='qwen_text',enabled=True)]:
   with self.assertRaises(HTTPException):workbench.save_setup(workbench.SetupConfig(**payload))
  self.assertEqual((self.root/'.env').read_bytes(),before)

class ReplayTests(unittest.TestCase):
 setUp=test_interactive_expansion.ExpansionTests.setUp
 tearDown=test_interactive_expansion.ExpansionTests.tearDown
 def test_static_assertion_stops_failed_branch_and_preserves_original(self):
  before=project.sources(self.rid);binding=replay.preview_binding(uuid.UUID(self.rid))
  yes=replay.Branch(label='文件存在',decisions=[replay.Decision(action='check_file',path='src/App.tsx',check='exists')])
  no=replay.Branch(label='缺少文字',decisions=[replay.Decision(action='check_file',path='src/App.tsx',check='contains',expected='not-existing-unique'),replay.Decision(action='finish')])
  result=replay.compare(uuid.UUID(self.rid),replay.ReplayInput(token=binding['token'],branches=[yes,no]))
  self.assertEqual(result['branches'][0]['static_checks'],{'total':1,'passed':1});self.assertEqual(result['branches'][1]['stop_reason'],'源码断言未通过')
  self.assertEqual(project.sources(self.rid),before);self.assertEqual(result['provider_calls'],0)
  exported=replay.export_branch(uuid.UUID(self.rid),uuid.UUID(result['id']));self.assertIn(b'fixed_source_decisions',exported.body)
 def test_read_receipt_uses_same_project_and_exact_fingerprint_without_execution(self):
  tools.execute(tools.ToolContext(self.rid,'task','original'),'project.list_files',{})
  receipt=replay.receipt_catalog(self.rid)[0]
  decision=replay.Decision(action='read_receipt',receipt_id=receipt['id'],receipt_sha256=receipt['sha256'])
  with patch.object(tools,'execute',side_effect=AssertionError('must never run tool')):
   value=replay.read_receipt(self.rid,decision)
  self.assertEqual(value['tool'],'project.list_files')
  with self.assertRaises(ValueError):replay.read_receipt(str(uuid.uuid4()),decision)
  decision.receipt_sha256='0'*64
  with self.assertRaises(ValueError):replay.read_receipt(self.rid,decision)
 def test_assertion_contract_rejects_missing_values_and_escaping_path(self):
  with self.assertRaises(ValidationError):replay.Decision(action='check_file',path='src/App.tsx',check='contains')
  with self.assertRaises(ValueError):replay.apply_fixed({},replay.Decision(action='check_file',path='../.env',check='exists'))

class CreationTests(unittest.TestCase):
 setUp=test_interactive_expansion.ExpansionTests.setUp
 tearDown=test_interactive_expansion.ExpansionTests.tearDown
 def test_template_role_selection_is_frozen_into_run_and_checkpoint_input(self):
  from app import capability_templates,main
  from fastapi import BackgroundTasks
  selected={'reviewer':'qwen3.8-flash','developer':'deepseek-v4-pro'}
  draft=capability_templates.draft('tasks',capability_templates.DraftInput(role_models=selected))
  proposal=designs.get_design(draft['design_id']);self.assertEqual(proposal['role_models'],selected)
  background=BackgroundTasks()
  with patch.object(main,'active_runs',set()),patch.object(model,'validate_route') as guard,patch.object(main,'public_run',side_effect=lambda state:state):
   state=main.create_run(main.RunRequest(design_id=draft['design_id'],design_revision=1),background)
  self.assertEqual(state['role_models'],{'planner':'deepseek-flash',**selected})
  self.assertEqual(store.get_run(state['run_id'])['role_models'],state['role_models'])
  self.assertEqual(background.tasks[0].args[1]['role_models'],state['role_models'])
  self.assertEqual(state['review_context_version'],2);guard.assert_called_once()
 def test_disabled_qwen_preflight_leaves_draft_unclaimed(self):
  from app import capability_templates,main
  from fastapi import BackgroundTasks
  draft=capability_templates.draft('tasks',capability_templates.DraftInput(role_models={'reviewer':'qwen3.8-flash'}))
  with patch.dict(os.environ,{'QWEN_TEXT_CALLS_ENABLED':'false'}),patch.object(model,'OpenAI',side_effect=AssertionError('must not construct client')):
   with self.assertRaises(HTTPException):main.create_run(main.RunRequest(design_id=draft['design_id'],design_revision=1),BackgroundTasks())
  self.assertEqual(designs.get_design(draft['design_id'])['status'],'ready')
  self.assertIsNone(store.get_run(draft['design_id']))

if __name__=='__main__':unittest.main()
