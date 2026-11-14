"""Offline regressions from the final three-case retest; no provider sockets."""
import io,json,sys,unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch,Mock
from contextlib import closing
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import model,team,team_policy,executor,review_context,tools,runner,dify,store,project
import test_team,test_executor_protocol,test_dify_offline

class CloseoutTests(unittest.TestCase):
 setUp=test_executor_protocol.ExecutorRecoveryTests.setUp
 def pending(self,version=3,**changes):
  task={**self.state['executor_tasks'][0],'status':'running','successful_tools':1}
  return {**self.state,'agent_protocol_version':version,'executor_tasks':[task],
          'pending_executor':{'turn':1,'decision':{'action':'complete_task','task_id':'task-1','summary':'已实现全部冻结要求，提交系统检查'}},**changes}
 def test_last_completed_task_submits_without_extra_model_turn_but_legacy_does_not(self):
  with patch.object(tools,'_ready'):
   self.assertTrue(executor.apply_decision(self.pending())['executor_done'])
   self.assertFalse(executor.apply_decision(self.pending(version=2))['executor_done'])
 def test_pending_tasks_are_not_automatically_completed(self):
  s=self.pending();s['executor_tasks'].append({**s['executor_tasks'][0],'id':'task-2','status':'pending','successful_tools':0,'depends_on':['task-1']})
  with patch.object(tools,'_ready') as ready:
   result=executor.apply_decision(s);self.assertFalse(result['executor_done']);ready.assert_not_called()
  self.assertEqual(result['executor_tasks'][1]['status'],'pending')
 def test_structural_readiness_failure_returns_task_to_development(self):
  with patch.object(tools,'_ready',side_effect=runner.SandboxError('缺少测试')):
   result=executor.apply_decision(self.pending())
  self.assertFalse(result['executor_done']);self.assertEqual(result['executor_tasks'][0]['status'],'running')
  self.assertNotIn('completion_summary',result['executor_tasks'][0])
 def test_final_turn_forbids_tools_and_resumes_same_frozen_schema(self):
  s={**self.state,'agent_protocol_version':3}
  ctx={'current_task':{'id':'task-1'},'step_budget':{'role_remaining':1}}
  seen=[]
  def generate(*a,**kw):seen.append(a[4]);return a[4](action='complete_task',task_id='task-1',summary='完成已有实现'),{}
  with patch.object(executor,'_context',return_value=ctx),patch.object(executor,'generate_json',side_effect=generate):
   first=executor.decide(s);second=executor.decide(s)
  self.assertEqual(seen,[model.TaskCloseoutDecision]);self.assertTrue(second['usage']['replayed']);self.assertEqual(first['decision'],second['decision'])
  with self.assertRaises(ValueError):model.TaskCloseoutDecision(action='tool',task_id='task-1',tool='project.read_file',arguments={'path':'src/App.tsx'},summary='不能继续读取')
 def test_focused_plan_accepts_one_complete_task_and_preserves_legacy_minimum(self):
  plan={'steps':[{'title':'完整任务页面','description':'实现全部功能、来源区域和交互测试','depends_on':[],'acceptance_indices':[0,1]}]}
  parsed=team.FocusedPlannerAction(action='submit',summary='合并为完整任务',plan=plan)
  owners=team_policy.coverage({'acceptance_criteria':['任务管理','展示参考来源']},parsed.plan,[])
  self.assertEqual(len(owners),2)
  with self.assertRaises(ValueError):model.PlanResult.model_validate(plan)
  with self.assertRaises(ValueError):model.FocusedPlanResult.model_validate({'steps':plan['steps']*4})

class RetainedEvidenceTests(unittest.TestCase):
 def receipt(self,path,content,offset=0,total=None,digest='current',turn=1,episode=0):
  return {'operation_id':f'reviewer_{episode}_{turn}','task_id':'reviewer','tool_name':'project.read_file','result':{'ok':True,'data':{'path':path,'content':content,'offset':offset,'total_characters':total or len(content),'sha256':digest}}}
 def manifest(self):return {'files':{p:{'sha256':'current'} for p in ['src/App.tsx','src/style.css','tests/app.spec.ts']}}
 def test_early_full_body_survives_last_five_eviction_and_partial_reread(self):
  body='A'*8000+'B'*405
  h=[self.receipt('src/App.tsx',body)]+[self.receipt('src/style.css','body{}',turn=i) for i in range(2,7)]+[self.receipt('src/App.tsx',body[8000:],offset=8000,total=8405,turn=7)]
  p=review_context.retained_source_evidence(self.manifest(),h,0)
  app=next(x for x in p['files'] if x['path']=='src/App.tsx')
  self.assertEqual(app['segments'][0]['content'],body);self.assertTrue(app['full_file_present']);self.assertEqual(app['omitted_read_characters'],0)
 def test_stale_previous_episode_and_unread_bodies_do_not_reappear(self):
  h=[self.receipt('src/App.tsx','stale',digest='old'),self.receipt('src/style.css','old episode',episode=1),self.receipt('tests/app.spec.ts','known')]
  p=review_context.retained_source_evidence(self.manifest(),h,0)
  self.assertEqual([x['path'] for x in p['files']],['tests/app.spec.ts'])
 def test_bounded_serialized_payload_marks_exact_retained_ranges_and_gaps(self):
  body='"中文\\\n'*5000
  h=[self.receipt('src/App.tsx',body),self.receipt('src/style.css','x'*10000)]
  p=review_context.retained_source_evidence(self.manifest(),h,0,budget=1600)
  self.assertLessEqual(len(json.dumps(p,ensure_ascii=False)),1600)
  a=p['files'][0];self.assertFalse(a['full_file_present']);self.assertGreater(a['omitted_read_characters'],0)
  for part in a['segments']:self.assertEqual(body[part['start']:part['end']],part['content'])
  p=review_context.retained_source_evidence(self.manifest(),[self.receipt('src/App.tsx','abc',0,10),self.receipt('src/App.tsx','xyz',7,10)],0)
  self.assertFalse(p['files'][0]['full_file_present']);self.assertEqual(len(p['files'][0]['segments']),2)
 def test_supported_requires_body_in_current_packet_not_only_read_metadata(self):
  s={'run_id':'synthetic','agent_protocol_version':3,'acceptance_criteria':['显示任务'],'repair_round':0}
  ctx={'manifest':self.manifest(),'technical':dict.fromkeys(['build_passed','test_passed','health_passed','environment_verified'],True),'source_evidence':{'files':[]}}
  h=[self.receipt('src/App.tsx','body')]
  report=team.EvidenceReviewReport(verdict='ready_for_manual',summary='需要当前证据',criteria=[team.EvidenceCriterionReview(index=0,status='supported',evidence='实际正文支持要求',files=['src/App.tsx'])])
  with patch.object(tools,'history',return_value=h),patch.object(runner,'framework_files',return_value={}):
   with self.assertRaises(team.ReviewEvidenceError):team.validate_review(s,report,ctx,0)
   ctx['source_evidence']=review_context.retained_source_evidence(ctx['manifest'],h,0);team.validate_review(s,report,ctx,0)

class FailureDiagnosticsTests(unittest.TestCase):
 setUp=test_team.TeamTests.setUp
 def test_safe_http_classification_preserves_reserve_and_never_retries(self):
  request=httpx.Request('POST','https://example.invalid/PRIVATE',headers={'Authorization':'PRIVATE'})
  state={**self.state,'run_budget_cny':2,'model_call_limit':8};store.create_run(state['run_id'],'synthetic',state,'2026-11-14')
  for status,category in [(400,'request_rejected'),(401,'authentication'),(429,'rate_limit'),(503,'provider_server')]:
   error=model.APIStatusError('PRIVATE synthetic-secret',response=httpx.Response(status,request=request),body={'secret':'PRIVATE'})
   client=Mock();client.chat.completions.create.side_effect=error
   with patch.object(model,'_client',return_value=client),patch.object(model,'_get_settings',return_value={'provider':'deepseek','model':'deepseek-flash','budget_cny':99}):
    with self.assertRaises(model.ModelCallFailed) as caught:model.generate_json(state['run_id'],'error-'+str(status),'system','test',model.RepairDecision,100)
   self.assertIn(category,str(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception));self.assertIsNone(caught.exception.__cause__)
   self.assertEqual(client.chat.completions.create.call_count,1)
  with model._db_connection() as c:
   rows=c.execute('SELECT u.status,u.actual_cny,u.reserve_cny,e.category,e.http_status FROM model_usage u JOIN model_call_errors e ON u.id=e.call_id').fetchall()
  self.assertEqual(len(rows),4);self.assertTrue(all(x['status']=='uncertain' and x['actual_cny'] is None and x['reserve_cny']>0 for x in rows))
 def test_timeout_connection_and_unknown_do_not_expose_raw_messages(self):
  request=httpx.Request('POST','https://example.invalid')
  for error,category in [(model.APITimeoutError(request),'timeout'),(model.APIConnectionError(request=request,message='PRIVATE'),'connection'),(ValueError('PRIVATE'),'unknown')]:
   d=model.safe_request_failure(error);self.assertEqual(d['category'],category);self.assertNotIn('PRIVATE',json.dumps(d))

class RetrievalContractTests(unittest.TestCase):
 setUp=test_dify_offline.DifyOfflineTests.setUp
 transport=test_dify_offline.DifyOfflineTests.transport
 result=test_dify_offline.DifyOfflineTests.result
 def test_long_query_is_bounded_before_transport_and_truncation_is_visible(self):
  transport=self.transport(self.result());value=dify.capture([self.dataset],'资料'*500)
  payload=json.loads(transport.requests[0][0].data);self.assertEqual(len(payload['query']),250)
  self.assertTrue(value['query_truncated']);self.assertFalse(payload['retrieval_model']['reranking_enable']);self.assertEqual(payload['retrieval_model']['search_method'],'semantic_search')
  self.assertNotIn('reranking_model',payload['retrieval_model'])
 def test_empty_query_and_invalid_limits_stop_before_reservation(self):
  transport=self.transport(self.result())
  for q,k,size in [('   ',3,6000),('query',0,6000),('query',True,6000),('query',4,6000),('query',3,0),('query',3,6001)]:
   with self.assertRaises(dify.DifyError):dify.capture([self.dataset],q,k,size)
  self.assertFalse(transport.requests)
  with closing(dify.connect()) as c:self.assertEqual(c.execute('SELECT count(*) FROM calls').fetchone()[0],0)
 def test_parameter_diagnostics_retain_only_known_fields_not_values_or_messages(self):
  body={'code':'invalid_param','message':'PRIVATE','errors':{'retrieval_model.top_k':'PRIVATE value','query':'PRIVATE query','PRIVATE_field':'PRIVATE'}}
  transport=self.transport(HTTPError('https://api.dify.ai',400,'PRIVATE',{},io.BytesIO(json.dumps(body).encode())))
  with self.assertRaises(dify.DifyError) as caught:dify.capture([self.dataset],'query')
  self.assertIn('retrieval_model.top_k',str(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception));self.assertEqual(len(transport.requests),1)
  self.assertNotIn('PRIVATE',json.dumps(dify.status()));self.assertIn('query',dify.status()['last_error']['message'])
 def test_original_short_query_payload_is_unchanged_not_a_claim_of_cloud_repair(self):
  self.assertEqual(dify.retrieval_payload('任务 标题 ACCEPTANCE'),{'query':'任务 标题 ACCEPTANCE','retrieval_model':{'search_method':'semantic_search','reranking_enable':False,'top_k':3,'score_threshold_enabled':False}})

if __name__=='__main__':unittest.main()
