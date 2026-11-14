"""Coverage failures replayed offline. No remote providers or user data."""
import io,json,sys,tempfile,unittest,uuid
from pathlib import Path
from contextlib import closing
from unittest.mock import patch,Mock
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import action_protocol,model,team,team_policy,tools,runner,project,store,executor,dify
import test_team,test_agent_protocol_repair,test_dify_offline

class RepairSchemaTests(unittest.TestCase):
 def test_actual_two_broken_payloads_are_not_guessed_or_executed(self):
  records=json.loads((Path(__file__).parent/'fixtures/coverage_action_failures.json').read_text())['records']
  self.assertEqual(len(records),2)
  with patch.object(tools,'execute') as execute:
   for item in records:
    with self.assertRaises(ValueError):model.parse_model_json(item['content'],model.SimpleRepairDecision)
   execute.assert_not_called()
 def test_recovery_schema_requires_one_flat_patch_or_read_or_stop(self):
  schema=model.SimpleRepairDecision.model_json_schema();self.assertEqual(len(schema['oneOf']),3)
  patch_branch=schema['oneOf'][0];self.assertEqual(patch_branch['properties']['tool']['const'],'project.patch_file')
  self.assertEqual(set(patch_branch['properties']['arguments']['required']),{'path','expected_sha256','before','after'})
  action={'action':'tool','summary':'单文件修复','task_id':'task-1','tool':'project.patch_file','arguments':{'path':'src/style.css','expected_sha256':'a'*64,'before':'one','after':'two'}}
  parsed=model.parse_model_json(json.dumps(action),model.SimpleRepairDecision);self.assertEqual(parsed.arguments,action['arguments'])
  for bad in [{**action,'tool':'project.apply_changes'},{**action,'arguments':{**action['arguments'],'edits':[]}},{**action,'arguments':{'path':'src/a.tsx','before':'a','after':'b'}}]:
   with self.assertRaises(ValueError):model.SimpleRepairDecision.model_validate(bad)
 def test_legacy_schema_and_roles_do_not_gain_patch_tool(self):
  self.assertNotIn('project.patch_file',json.dumps(model.BoundedExecutorDecision.model_json_schema()))
  for role in ['planner','reviewer','developer']:self.assertFalse(team.allowed(role,'project.patch_file',{'agent_protocol_version':1}))
  self.assertTrue(team.allowed('developer','project.patch_file',{'agent_protocol_version':2}))
  self.assertFalse(team.allowed('reviewer','project.patch_file',{'agent_protocol_version':2}))
 def test_contract_navigation_preserves_exact_frozen_names(self):
  state={'acceptance_criteria':['按钮名称删除+标题，不加冒号','业务数据保留'],'product_spec':{'data_notes':['输入标签修改标题']}}
  before=json.dumps(state);value=action_protocol.interface_contract(state)
  self.assertEqual(value['criteria'],[{'index':0,'text':state['acceptance_criteria'][0]}]);self.assertEqual(json.dumps(state),before)

class PatchToolTests(unittest.TestCase):
 def test_patch_inherits_digest_unique_match_path_and_durable_replay(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(runner,'ROOT',Path(tmp)),patch.object(runner,'GENERATED_ROOT',Path(tmp)/'generated'):
   rid=str(uuid.uuid4());runner.initialize_workspace(rid);old=project.sources(rid)['src/App.tsx']
   context=tools.ToolContext(rid,'task-1','single-patch')
   args={'path':'src/App.tsx','expected_sha256':project.digest(old),'before':'开始添加你的第一项任务。','after':'修复后的说明。'}
   value=tools.execute(context,'project.patch_file',args);self.assertTrue(value['ok'],value)
   digest=project.manifest(rid)['digest'];again=tools.execute(context,'project.patch_file',args)
   self.assertTrue(again['replayed']);self.assertEqual(project.manifest(rid)['digest'],digest)
   for index,bad in enumerate([{**args,'after':'另一个内容'},{**args,'path':'../../private.txt'},{**args,'before':'not-existing','expected_sha256':project.digest(project.sources(rid)['src/App.tsx'])}]):
    denied=tools.execute(tools.ToolContext(rid,'task-1','bad-'+str(index)),'project.patch_file',bad)
    self.assertFalse(denied['ok']);self.assertEqual(project.manifest(rid)['digest'],digest)

class ReviewChecksTests(unittest.TestCase):
 setUp=test_agent_protocol_repair.EvidenceTests.setUp
 read=test_agent_protocol_repair.EvidenceTests.read
 def report(self,**overrides):
  value={'verdict':'ready_for_manual','summary':'当前源码与固定结果已核对','criteria':[{'index':0,'status':'supported','evidence':'已读取实现与测试支持保存功能','files':['src/App.tsx','tests/e2e.spec.ts']},{'index':1,'status':'supported','evidence':'样式与当前基础检查作为证据','files':['src/style.css'],'fixed_checks':['page_health']}]}
  value.update(overrides);return team.EvidenceReviewReport.model_validate(value)
 def test_current_fixed_check_is_supplemental_not_a_file_or_sole_proof(self):
  self.state['agent_protocol_version']=2
  history=[self.read('src/App.tsx','app'),self.read('tests/e2e.spec.ts','test'),self.read('src/style.css','css')]
  with patch.object(tools,'history',return_value=history),patch.object(runner,'framework_files',return_value={}):
   team.validate_review(self.state,self.report(),self.ctx,0)
   bad=self.report();bad.criteria[1].files=['tests/standard/health.spec.ts']
   with self.assertRaises(team.ReviewEvidenceError):team.validate_review(self.state,bad,self.ctx,0)
   bad.criteria[1].files=[]
   with self.assertRaises(team.ReviewEvidenceError):team.validate_review(self.state,bad,self.ctx,0)
   self.ctx['technical']['health_passed']=False
   with self.assertRaisesRegex(runner.SandboxError,'固定检查引用'):team.validate_review(self.state,self.report(),self.ctx,0)
 def test_final_schema_forbids_tools_without_loosening_report(self):
  schema=team.FinalReviewerAction.model_json_schema()
  self.assertEqual([b['properties']['action']['const'] for b in schema['oneOf']],['submit','stop'])
  with self.assertRaises(ValueError):team.FinalReviewerAction(action='tool',summary='不能继续读',tool='project.read_file',arguments={'path':'src/App.tsx'})
  with self.assertRaises(ValueError):team.EvidenceCriterionReview(index=0,status='supported',evidence='不允许任意检查标识',fixed_checks=['all_business_passed'])

class ReviewBudgetTests(unittest.TestCase):
 setUp=test_team.TeamTests.setUp
 def test_two_final_turns_are_reserved_and_total_steps_still_stop_at_forty(self):
  self.state.update(agent_protocol_version=2,team_policy_version=1,execution_step_limit=40,specialist_turn=8)
  self.assertEqual(team.role_limit(self.state,'reviewer'),10)
  self.assertEqual(team_policy.reserve(self.state)['steps'],8)
  ctx={'basis':{'environment':{'id':'frozen'}},'step_budget':{'role_remaining':2,'last_step':False}}
  schemas=[]
  def provider(*a,**k):schemas.append(a[4]);return a[4](action='stop',summary='证据不足，停止'),{}
  with patch.object(team,'context',return_value=ctx),patch.object(team,'basis',return_value=ctx['basis']):team.step(self.state,'reviewer',provider)
  self.assertIs(schemas[0],team.FinalReviewerAction)
  for i in range(39):team.invoke(self.state,'reviewer','filler-'+str(i),'system','test',team.SpecialistDecision,100,self.provider)
  with self.assertRaises(team.TeamLimitExceeded):team.invoke(self.state,'reviewer','extra','system','test',team.SpecialistDecision,100,self.provider)
  self.assertEqual(team.count(self.state['run_id']),40)

class DifyDiagnosticTests(unittest.TestCase):
 setUp=test_dify_offline.DifyOfflineTests.setUp
 transport=test_dify_offline.DifyOfflineTests.transport
 def test_allowlisted_error_is_retained_without_message_or_key_and_no_retry(self):
  raw=json.dumps({'code':'provider_quota_exceeded','message':'PRIVATE synthetic-local-test-key','extra':'PRIVATE'}).encode()
  transport=self.transport(HTTPError('https://api.dify.ai/v1/datasets',400,'private',{},io.BytesIO(raw)))
  with self.assertRaises(dify.DifyError) as caught:dify.capture([self.dataset],'合成检索')
  self.assertIn('provider_quota_exceeded',str(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception))
  with self.assertRaises(dify.DifyError):dify.capture([self.dataset],'不允许重试')
  self.assertEqual(len(transport.requests),1)
  status=dify.status();self.assertEqual(status['last_error']['code'],'provider_quota_exceeded');self.assertEqual(status['reserved_credits'],5)
  self.assertNotIn('PRIVATE',json.dumps(status))
  with closing(dify.connect()) as c:self.assertEqual(c.execute('SELECT error_code FROM call_errors').fetchone()[0],'provider_quota_exceeded')
 def test_unknown_large_or_malformed_bodies_do_not_leak(self):
  for raw in [b'PRIVATE not-json',json.dumps({'code':'PRIVATE malicious','message':'PRIVATE'}).encode(),b'x'*8193]:
   with self.subTest(raw=raw[:20]):
    error=HTTPError('https://api.dify.ai',400,'private',{},io.BytesIO(raw));self.assertEqual(dify.safe_error_code(error),'unknown')
 def test_old_http_record_is_unknown_and_does_not_clear_reserved_credits(self):
  with closing(dify.connect()) as c:c.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?)',('old',1,'POST','/datasets/x/retrieve','400','retrieve',5));c.commit()
  value=dify.status();self.assertEqual(value['last_error']['code'],'unknown');self.assertEqual(value['reserved_credits'],5)

if __name__=='__main__':unittest.main()
