"""Offline regressions from the paid pilot: no provider requests, no real workspace writes."""
import json,sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import model,team,team_policy,review_context,executor,runner,tools,store,graph
import test_team,test_executor_protocol

class FormatTests(unittest.TestCase):
 def test_actual_pilot_failures_are_rejected_without_guessing_actions(self):
  records=json.loads((Path(__file__).parent/'fixtures/pilot_action_failures.json').read_text())['records']
  self.assertEqual(len(records),7)
  for item in records:
   cls=team.ReviewerAction if item['role']=='reviewer' else model.BoundedExecutorDecision
   with self.subTest(stage=item['stage']),self.assertRaises(ValueError):model.parse_model_json(item['content'],cls)
 def test_array_multiobject_and_duplicate_fields_have_specific_feedback(self):
  for raw,match in [('[{}]','不能是数组'),('{} {}','多个JSON'),('{"action":"stop","action":"tool"}','字段重复')]:
   with self.subTest(raw=raw),self.assertRaisesRegex(ValueError,match):model.parse_model_json(raw,team.ReviewerAction)
 def test_missing_tool_is_named_in_feedback_and_no_alias_is_inferred(self):
  with self.assertRaisesRegex(ValueError,'缺少tool'):
   model.parse_model_json('{"action":"tool","summary":"读取文件","arguments":{"path":"src/App.tsx"}}',team.ReviewerAction)
 def test_schema_only_exposes_role_actions_and_required_fields(self):
  for cls,expected in [(team.ReviewerAction,{'tool','review'}),(team.PlannerAction,{'tool','plan'}),(model.BoundedExecutorDecision,{'tool','task_id'})]:
   schema=cls.model_json_schema();tool=schema['oneOf'][0]
   self.assertIn('tool',tool['required']);self.assertIn('arguments',tool['required']);self.assertNotIn('null',json.dumps(tool['properties']['tool']))
   fields={p for branch in schema['oneOf'] for p in branch['properties']}
   self.assertTrue(expected<=fields)
   if cls is team.ReviewerAction:
    self.assertNotIn('plan',fields);self.assertNotIn('PlanResult',schema.get('$defs',{}))
   if cls is team.PlannerAction:
    self.assertNotIn('review',fields);self.assertNotIn('ReviewReport',schema.get('$defs',{}))
 def test_corrected_single_action_and_serialized_receipt_replay(self):
  raw='{"action":"tool","summary":"读取实现","tool":"project.read_file","arguments":{"path":"src/App.tsx"}}'
  value=model.parse_model_json(raw,team.ReviewerAction)
  self.assertEqual(value,team.ReviewerAction.model_validate_json(value.model_dump_json()))
  with self.assertRaises(ValueError):team.ReviewerAction.model_validate({**json.loads(raw),'review':{'verdict':'blocked','summary':'还没有完成审查','criteria':[{'index':0,'status':'manual','evidence':'需要进一步核对'}]}})

class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.state={'run_id':'synthetic','agent_protocol_version':1,'acceptance_criteria':['保存刷新后保留','手机布局'],'repair_round':0}
  self.manifest={'files':{'src/App.tsx':{'sha256':'app'},'tests/e2e.spec.ts':{'sha256':'test'},'src/style.css':{'sha256':'css'}},'framework':{'src/main.tsx':'main'}}
  self.ctx={'manifest':self.manifest,'technical':dict.fromkeys(('build_passed','test_passed','health_passed','environment_verified'),True)}
 def read(self,path,digest,turn=1,episode=0,content='verified fragment'):
  return {'operation_id':f'reviewer_{episode}_{turn}','tool_name':'project.read_file','result':{'ok':True,'data':{'path':path,'sha256':digest,'content':content,'offset':0}}}
 def report(self):
  return team.ReviewReport(verdict='ready_for_manual',summary='当前源码支持要求',criteria=[team.CriterionReview(index=0,status='supported',evidence='实现与测试均已经检查',files=['src/App.tsx','tests/e2e.spec.ts']),team.CriterionReview(index=1,status='supported',evidence='手机布局已检查对应样式',files=['src/style.css'])])
 def test_rejection_lists_every_missing_file_and_criterion_then_accepts_reads(self):
  history=[self.read('src/App.tsx','app')]
  with patch.object(tools,'history',return_value=history),patch.object(runner,'framework_files',return_value={}):
   with self.assertRaises(team.ReviewEvidenceError) as caught:team.validate_review(self.state,self.report(),self.ctx,0)
   details=caught.exception.details
   self.assertEqual(details['read_next'],['src/style.css','tests/e2e.spec.ts'])
   self.assertEqual([x['index'] for x in details['criteria']],[0,1])
   history.extend([self.read('tests/e2e.spec.ts','test',2),self.read('src/style.css','css',3)])
   team.validate_review(self.state,self.report(),self.ctx,0)
 def test_unknown_files_are_never_suggested_for_reading(self):
  report=self.report();report.criteria[0].files=['../../private.txt']
  with patch.object(tools,'history',return_value=[]):
   with self.assertRaises(team.ReviewEvidenceError) as caught:team.validate_review(self.state,report,self.ctx,0)
   self.assertIn('../../private.txt',caught.exception.details['unknown_files'])
   self.assertNotIn('../../private.txt',caught.exception.details['read_next'])
 def test_only_current_nonempty_same_episode_read_receipts_count(self):
  history=[self.read('src/App.tsx','old'),self.read('src/style.css','css',episode=1),self.read('tests/e2e.spec.ts','test',content=''),self.read('src/main.tsx','main')]
  self.assertEqual([r['path'] for r in review_context.current_reads(self.manifest,history,0)],['src/main.tsx'])
 def test_complete_read_index_retains_early_receipts_and_framework(self):
  history=[self.read('src/App.tsx','app'),self.read('src/main.tsx','main')]+[self.read('src/style.css','css',i) for i in range(3,9)]
  value=review_context.augment({'manifest':self.manifest},self.state,{},history)
  self.assertEqual(value['review_packet']['current_read_files'],['src/App.tsx','src/main.tsx','src/style.css'])
 def test_missing_files_list_does_not_become_supported_automatically(self):
  report=self.report();report.criteria[0].files=[]
  with patch.object(tools,'history',return_value=[]):
   with self.assertRaises(team.ReviewEvidenceError) as caught:team.validate_review(self.state,report,self.ctx,0)
   self.assertTrue(caught.exception.details['criteria'][0]['missing_files'])

class BudgetAndReplayTests(unittest.TestCase):
 setUp=test_team.TeamTests.setUp
 def configure(self):
  self.state.update(agent_protocol_version=1,team_policy_version=1,execution_step_limit=40,run_budget_cny=2,model_call_limit=80)
 def invoke(self,role,stage):
  return team.invoke(self.state,role,stage,'system','synthetic',team.ReviewerAction,100,self.provider)
 def test_eight_review_slots_are_reserved_inside_original_forty(self):
  self.configure()
  for i in range(32):self.invoke('developer',f'executor-{i}')
  with self.assertRaises(team.ReviewReserveReached):self.invoke('developer','executor-next')
  for i in range(8):self.invoke('reviewer',f'agent-reviewer-0-{i}')
  with self.assertRaises(team.TeamLimitExceeded):self.invoke('reviewer','agent-reviewer-0-9')
  self.assertEqual(team.count(self.state['run_id']),40)
  self.assertEqual(team_policy.reserve(self.state)['estimated_cny'],.75)
 def test_old_tasks_still_reserve_three_steps(self):
  self.state.update(team_policy_version=1,execution_step_limit=40)
  self.assertEqual(team_policy.reserve(self.state)['steps'],3)
 def test_actual_money_and_call_limit_are_not_expanded(self):
  self.configure();self.state.update(model_call_limit=8,run_budget_cny=.25)
  store.create_run(self.state['run_id'],'synthetic',self.state,'2026-11-14')
  with self.assertRaises(team.ReviewReserveReached):model._reserve(self.state['run_id'],'executor-1','fixture','deepseek-flash',10,.25)
  model._reserve(self.state['run_id'],'agent-reviewer-0-1','fixture','qwen3.8-flash',10,.25)
  with self.assertRaises(model.BudgetExceeded):model._reserve(self.state['run_id'],'agent-reviewer-0-2','fixture','qwen3.8-flash',10,.25)
 def test_remaining_step_counts_include_current_call(self):
  self.configure();self.state['specialist_turn']=7
  value=team_policy.step_budget(self.state,39,'reviewer',8)
  self.assertEqual(value['role_remaining'],1);self.assertTrue(value['last_step'])
  self.assertEqual(team_policy.step_budget(self.state,31,'developer')['role_remaining'],1)
 def test_last_review_step_cannot_spend_final_turn_on_tool(self):
  self.configure();self.state['specialist_turn']=7
  ctx={'basis':{'environment':{'id':'fake'}},'step_budget':{'last_step':True}}
  value=team.ReviewerAction(action='tool',summary='读取文件',tool='project.read_file',arguments={'path':'src/App.tsx'})
  with patch.object(team,'context',return_value=ctx),patch.object(team,'basis',return_value=ctx['basis']),patch.object(tools,'execute') as execute:
   with self.assertRaises(team.TeamLimitExceeded):team.step(self.state,'reviewer',lambda *a,**k:(value,{}))
   execute.assert_not_called()
 def test_saved_request_replays_with_frozen_remaining_budget_not_new_count(self):
  self.configure()
  ctx={'basis':{'environment':{'id':'fixed'}},'step_budget':{'last_step':False}}
  action=team.ReviewerAction(action='tool',summary='读取文件',tool='project.read_file',arguments={'path':'src/App.tsx'})
  provider=Mock(return_value=(action,{}))
  receipt={'ok':True,'data':{}}
  with patch.object(team,'context',return_value=ctx),patch.object(team,'basis',return_value=ctx['basis']),patch.object(tools,'execute',return_value=receipt):
   first=team.step(self.state,'reviewer',provider)
   changed={**ctx,'step_budget':{'last_step':True}}
   with patch.object(team,'context',return_value=changed):second=team.step(self.state,'reviewer',provider)
  self.assertEqual(first['decision'],second['decision']);provider.assert_called_once()
  self.assertTrue(second['usage']['replayed'])

class ProgressTests(unittest.TestCase):
 setUp=test_executor_protocol.ExecutorRecoveryTests.setUp
 def test_warning_precedes_unchanged_eight_step_stop(self):
  self.state.update(agent_protocol_version=1,project_manifest={'digest':'test-digest'},executor_no_progress=3)
  self.state['executor_tasks'][0]['status']='running'
  self.state['pending_executor']={'turn':1,'decision':{'action':'tool','task_id':'task-1','tool':'project.list_files','arguments':{},'summary':'读取清单'},'usage':{}}
  with patch.object(tools,'execute',return_value={'ok':True,'data':{}}):
   result=executor.apply_decision(self.state);self.assertFalse(result['executor_stopped']);self.assertIn('达到8步',result['executor_feedback'])
   self.state['executor_no_progress']=7
   result=executor.apply_decision(self.state);self.assertTrue(result['executor_stopped']);self.assertEqual(result['executor_stop_reason'],'no_progress')

if __name__=='__main__':unittest.main()
