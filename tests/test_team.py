"""Role boundaries and durable coordination, isolated from private data and providers."""
import json,sys,tempfile,unittest,uuid,hashlib
from pathlib import Path
from unittest.mock import Mock,patch
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import team,tools,model,runner,store,project,executor
from pydantic import ValidationError

class TeamTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for item in [patch.object(runner,'ROOT',self.root),patch.object(model,'DATA_DIR',self.root/'data'),patch.object(model,'USAGE_DB',self.root/'data/usage.sqlite3'),patch.object(store,'DATA_DIR',self.root/'data'),patch.object(store,'RUNS_DB',self.root/'data/runs.sqlite3')]:item.start();self.addCleanup(item.stop)
        self.state={'run_id':str(uuid.uuid4()),'orchestration_version':1,'execution_step_limit':3,'model_name':'deepseek-flash'}
        self.value=team.SpecialistDecision(action='stop',summary='没有足够依据，停止')
        self.provider=Mock(return_value=(self.value,{'estimated_cost_cny':.01}))
    def invoke(self,role='planner',stage='one',provider=None):
        return team.invoke(self.state,role,stage,'system','user',team.SpecialistDecision,100,provider or self.provider)
    def test_roles_have_distinct_server_enforced_permissions(self):
        for role in ('planner','reviewer','unregistered'):
            for name in ('project.apply_changes','git.restore','git.snapshot','project.request_dependency','web.search','web.read'):
                with self.subTest(role=role,name=name),patch.object(project,'project_lock',side_effect=AssertionError('must not execute')):
                    value=tools.execute(tools.ToolContext(self.state['run_id'],'task','op','generated',role),name,{})
                    self.assertEqual(value['error']['code'],'role_permission_denied')
        self.assertTrue(team.allowed('developer','project.apply_changes',{}))
        self.assertTrue(team.allowed('reviewer','project.test',{}))
        self.assertFalse(team.allowed('planner','project.test',{}))
        self.assertFalse(team.allowed('developer','web.read',{}))
    def test_completed_call_replayed_without_charging_step_or_provider(self):
        first=self.invoke();second=self.invoke()
        self.assertEqual(first[0],second[0]);self.assertTrue(second[1]['replayed'])
        self.assertEqual(team.count(self.state['run_id']),1);self.provider.assert_called_once()
    def test_all_roles_share_step_limit_across_rounds(self):
        for role in team.ROLE_NAMES:self.invoke(role)
        with self.assertRaises(team.TeamLimitExceeded):self.invoke('reviewer','round-two')
        self.assertEqual(self.provider.call_count,3)
        self.assertEqual({c.args[0] for c in self.provider.call_args_list},{self.state['run_id']})
    def test_uncertain_call_does_not_repeat_provider_on_resume(self):
        failing=Mock(side_effect=model.ModelCallFailed('uncertain'))
        with self.assertRaises(model.ModelCallFailed):self.invoke(provider=failing)
        with self.assertRaisesRegex(model.ModelCallFailed,'没有完整回执'):self.invoke()
        self.provider.assert_not_called();failing.assert_called_once();self.assertEqual(team.count(self.state['run_id']),1)
    def test_response_receipt_recovers_crash_before_agent_save(self):
        def interrupted(*a,**kw):
            prompt='user\n输出必须符合此 JSON Schema：\n'+json.dumps(team.SpecialistDecision.model_json_schema(),ensure_ascii=False)
            fingerprint=hashlib.sha256(json.dumps({'system':'system','user':prompt,'model':'deepseek-flash','max_tokens':100},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            with model._db_connection() as conn:conn.execute('INSERT INTO model_receipts VALUES (?,?,?,?,?)',(self.state['run_id'],'one',fingerprint,self.value.model_dump_json(),'{}'))
            raise RuntimeError('crash')
        with self.assertRaises(RuntimeError):self.invoke(provider=interrupted)
        restored,usage=self.invoke();self.assertEqual(restored,self.value);self.assertTrue(usage['replayed']);self.provider.assert_not_called()
    def test_preflight_block_can_resume_without_losing_step(self):
        for failure in (model.BudgetExceeded,model.ModelNotConfigured):
            with self.assertRaises(failure):self.invoke(provider=Mock(side_effect=failure('blocked')))
            self.assertEqual(team.count(self.state['run_id']),0)
        self.invoke();self.assertEqual(team.count(self.state['run_id']),1)
    def test_invalid_response_is_durable_and_counts_as_step(self):
        failing=Mock(side_effect=model.ModelOutputInvalid('bad format',{'estimated_cost_cny':.02}))
        with self.assertRaises(model.ModelOutputInvalid):self.invoke(provider=failing)
        with self.assertRaises(model.ModelOutputInvalid):self.invoke()
        self.provider.assert_not_called();self.assertEqual(team.count(self.state['run_id']),1)
    def test_tool_contract_cannot_smuggle_submission_or_commands(self):
        for data in ({'action':'tool','summary':'read'}, {'action':'stop','summary':'stop','arguments':{'command':'shell'}}, {'action':'submit','summary':'nothing'}):
            with self.assertRaises(ValidationError):team.SpecialistDecision.model_validate(data)
    def report(self,**changes):
        return team.ReviewReport.model_validate({'verdict':'ready_for_manual','summary':'已有源码证据，仍需用户核对','criteria':[{'index':0,'status':'supported','evidence':'已读取关键实现，需用户确认','files':['src/App.tsx']}],**changes})
    def review(self,report,history=None,technical=None):
        state={**self.state,'acceptance_criteria':['可新增记录']}
        ctx={'manifest':{'files':{'src/App.tsx':{'sha256':'current'}}},'technical':technical or dict.fromkeys(('build_passed','test_passed','health_passed','environment_verified'),True)}
        default=[{'operation_id':'reviewer_0_1','tool_name':'project.read_file','result':{'ok':True,'data':{'path':'src/App.tsx','sha256':'current','content':'synthetic source'}}}]
        with patch.object(tools,'history',return_value=default if history is None else history):team.validate_review(state,report,ctx,0)
    def test_review_requires_complete_unique_criteria(self):
        for items in ([{'index':1,'status':'manual','evidence':'还需人工进行核对'}], [{'index':0,'status':'manual','evidence':'还需人工进行核对'}]*2):
            with self.assertRaisesRegex(runner.SandboxError,'漏掉或重复'):self.review(self.report(criteria=items))
    def test_review_cannot_cite_unread_or_previous_round_files(self):
        with self.assertRaisesRegex(runner.SandboxError,'未读取'):self.review(self.report(),history=[])
        with self.assertRaisesRegex(runner.SandboxError,'未读取'):self.review(self.report(),history=[{'operation_id':'reviewer_1_1','tool_name':'project.read_file','result':{'ok':True,'data':{'path':'src/App.tsx','sha256':'current','content':'synthetic source'}}}])
    def test_review_cannot_cite_old_file_revision(self):
        with self.assertRaisesRegex(runner.SandboxError,'未读取'):
            self.review(self.report(),history=[{'operation_id':'reviewer_0_1','tool_name':'project.read_file','result':{'ok':True,'data':{'path':'src/App.tsx','sha256':'old','content':'old source'}}}])
    def test_review_cannot_overrule_failed_technical_evidence(self):
        with self.assertRaisesRegex(runner.SandboxError,'没有足够'):self.review(self.report(),technical={'build_passed':False})
        with self.assertRaisesRegex(runner.SandboxError,'没有足够'):self.review(self.report(criteria=[{'index':0,'status':'gap','evidence':'实现仍有明确缺口','files':[]}]))
        self.review(self.report())
    def test_review_marks_manual_without_updating_human_acceptance(self):
        with patch('app.acceptance.save_reviews',side_effect=AssertionError('never mark human acceptance')):
            self.review(self.report(criteria=[{'index':0,'status':'manual','evidence':'动态数据仍需人工操作确认'}]))
    def test_three_roles_share_real_budget_ledger_without_network(self):
        from types import SimpleNamespace
        state={**self.state,'run_budget_cny':.50,'model_call_limit':80}
        store.create_run(state['run_id'],'synthetic',state,'2026-11-08')
        response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=self.value.model_dump_json()))],usage=None)
        client=Mock();client.chat.completions.create.return_value=response
        with patch.object(model,'_client',return_value=client),patch.object(model,'_get_settings',return_value={'provider':'deepseek','model':'deepseek-flash','budget_cny':99}):
            self.invoke('planner','agent-planner-0-1',provider=model.generate_json)
            self.invoke('developer','executor-1',provider=model.generate_json)
            with self.assertRaises(model.BudgetExceeded):self.invoke('reviewer','agent-reviewer-0-1',provider=model.generate_json)
        self.assertEqual(client.chat.completions.create.call_count,2)
        with model._db_connection() as conn:
            rows=conn.execute('SELECT run_id,reserve_cny,actual_cny FROM model_usage').fetchall()
        self.assertEqual(len(rows),2);self.assertTrue(all(row['run_id']==state['run_id'] and row['actual_cny'] is None for row in rows))
        self.assertEqual(team.count(state['run_id']),2)
    def test_source_change_during_review_rejects_saved_decision(self):
        state={**self.state,'specialist_turn':0}
        with patch.object(team,'context',return_value={'basis':{'digest':'before'}}),patch.object(team,'basis',return_value={'digest':'after'}):
            with self.assertRaisesRegex(runner.SandboxError,'已变化'):team.step(state,'reviewer',self.provider)
        self.provider.assert_called_once()
    def test_resume_cannot_reuse_review_after_source_changed(self):
        ctx={'basis':{'digest':'before'}}
        team.invoke(self.state,'reviewer','agent-reviewer-0-1','system',json.dumps(ctx),team.SpecialistDecision,100,self.provider)
        self.provider.reset_mock()
        with patch.object(team,'context',return_value={'basis':{'digest':'after'}}):
            with self.assertRaisesRegex(runner.SandboxError,'过期'):team.step(self.state,'reviewer',self.provider)
        self.provider.assert_not_called()
    def test_compatibility_keeps_old_runs_single_executor(self):
        self.assertFalse(team.enabled({'runtime_version':2}));self.assertTrue(team.enabled(self.state))


class TeamRoutingTests(unittest.TestCase):
    """Exercise actual LangGraph routing with local deterministic evidence."""
    def setUp(self):
        from app import graph,acceptance,runtime_identity
        self.graph=graph
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.records={};self.rounds=[]
        def review(state,role,provider):
            self.rounds.append(state.get('repair_round',0))
            return {'decision':{'action':'submit','summary':'发现缺口需要修复','review':{'verdict':'changes_requested','summary':'保持范围修复缺口','criteria':[]}},'usage':{'agent_id':'reviewer'},'turn':1,'feedback':'','tool_result':None,'basis':{'digest':'same'}}
        patches=[patch.object(graph,'get_run',side_effect=lambda rid:self.records.get(rid)),patch.object(graph,'update_run',side_effect=lambda rid,value:self.records.update({rid:value})),
            patch.object(graph,'cancellation',return_value=False),patch.object(graph,'get_pause_requested',return_value=False),
            patch.object(team,'basis',return_value={'digest':'same'}),patch.object(team,'step',side_effect=review),patch.object(team,'count',return_value=5),
            patch.object(project,'manifest',return_value={'digest':'source','files':{}}),patch.object(runtime_identity,'require',return_value={'digest':'environment'}),
            patch.object(acceptance,'health_hash',return_value='health'),patch.object(acceptance,'suite_hash',return_value='suite'),
            patch.object(tools,'execute',return_value={'ok':True,'data':{'passed':True,'exit_code':0,'output':'ok'}}),
            patch.object(executor,'decide',return_value={'turn':1,'decision':{'action':'finish','summary':'提交当前源码'},'usage':{}}),
            patch.object(executor,'apply_decision',return_value={'executor_done':True}),patch('app.research.run_snapshot',return_value=[]),
            patch.object(graph,'deliver',return_value={'trace':str(self.root/'trace.json')})]
        for item in patches:item.start();self.addCleanup(item.stop)
        self.flow=graph.build_graph(self.root/'checkpoint.sqlite3');self.addCleanup(self.flow.checkpointer.conn.close)
        self.config={'configurable':{'thread_id':'routing'},'recursion_limit':256}
        self.state={'run_id':'routing','runtime_version':3,'orchestration_version':1,'product_spec':{},'acceptance_criteria':['保留功能'],
            'repair_round':0,'repair_limit':3,'trace':[],'validation_ok':False,'build_status':'passed','test_status':'passed'}
    def test_repeated_review_rejection_stops_at_shared_three_repairs(self):
        self.flow.update_state(self.config,self.state,as_node='browser_test')
        result=self.flow.invoke(None,self.config)
        self.assertEqual(self.rounds,[0,1,2,3]);self.assertEqual(result['repair_round'],3)
        self.assertEqual(result['status'],'failed_validation');self.assertFalse(result['validation_ok'])
        self.assertEqual(len([h for h in result['agent_handoffs'] if h['from']=='reviewer' and h['to']=='developer']),3)
    def test_reviewer_limit_stops_without_starting_repair(self):
        with patch.object(team,'step',side_effect=team.TeamLimitExceeded('共享步骤耗尽')):
            self.flow.update_state(self.config,self.state,as_node='browser_test')
            result=self.flow.invoke(None,self.config)
        self.assertEqual(result['repair_round'],0);self.assertEqual(result['status'],'failed_validation')
        self.assertIn('耗尽',result['last_error'])
    def test_rejected_submission_is_bounded_without_changing_requirements(self):
        seen=[]
        def bad(state,*args):
            seen.append(state['specialist_turn'])
            if state['specialist_turn']>=team.ROLE_LIMITS['reviewer']:raise team.TeamLimitExceeded('审查步数耗尽')
            raise runner.SandboxError('伪造或遗漏证据')
        with patch.object(team,'step',side_effect=bad):
            self.flow.update_state(self.config,self.state,as_node='browser_test')
            result=self.flow.invoke(None,self.config)
        self.assertEqual(len(seen),9);self.assertEqual(result['acceptance_criteria'],self.state['acceptance_criteria'])
        self.assertEqual(result['status'],'failed_validation')

if __name__=="__main__":unittest.main()
