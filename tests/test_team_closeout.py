"""Five bounded closeout rules; provider calls and Docker are replaced locally."""
import json,sys,tempfile,unittest,uuid,zipfile,shutil
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import team,team_policy,model,runner,store,project,graph,runtime_identity

class PolicyTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for item in (patch.object(runner,'ROOT',self.root),patch.object(store,'DATA_DIR',self.root/'data'),patch.object(store,'RUNS_DB',self.root/'data/runs.sqlite3'),patch.object(model,'DATA_DIR',self.root/'data'),patch.object(model,'USAGE_DB',self.root/'data/usage.sqlite3')):
            item.start();self.addCleanup(item.stop)
        self.state={'run_id':str(uuid.uuid4()),'orchestration_version':1,'team_policy_version':1,'execution_step_limit':8,'run_budget_cny':5,'model_call_limit':80,'acceptance_criteria':['功能甲','功能乙','页面限制','视觉核对']}
        self.plan=model.PlanResult.model_validate({'steps':[{'title':'任务'+str(i),'description':'受控开发','depends_on':[],'acceptance_indices':[0,1] if i==1 else []} for i in range(1,4)]})
        self.basis={'source_digest':'a','environment':{'id':'fixed'}}
        self.report={'verdict':'changes_requested','summary':'需要修复明确缺口','criteria':[{'index':0,'status':'gap','files':['src/App.tsx'],'evidence':'缺少新增操作，需要补齐'}]}
    def call(self,role,stage):
        return team.invoke(self.state,role,stage,'system','synthetic',team.SpecialistDecision,100,lambda *a,**k:(team.SpecialistDecision(action='stop',summary='synthetic'),{}))
    def test_planner_cannot_omit_responsibility(self):
        with self.assertRaisesRegex(ValueError,'尚未分配'):team_policy.coverage(self.state,self.plan,[])
    def test_development_fixed_and_manual_owners_are_explicit(self):
        exceptions=[team.CoverageException(index=2,owner='fixed_check',check='page_health',reason='核对已确认的静态页面'),team.CoverageException(index=3,owner='manual',reason='用户确认视觉是否符合需求')]
        result=team_policy.coverage(self.state,self.plan,exceptions)
        self.assertEqual([x['owner'] for x in result],['development','development','fixed_check','manual'])
        with self.assertRaisesRegex(ValueError,'重复'):team_policy.coverage(self.state,self.plan,exceptions+[exceptions[0]])
    def test_coverage_contract_cannot_be_attached_to_tool_action(self):
        with self.assertRaises(ValueError):team.CloseoutDecision(action='tool',tool='project.list_files',summary='read',coverage_exceptions=[{'index':0,'owner':'manual','reason':'用户明确进行核对'}])
    def test_developer_reserves_three_steps_and_review_can_use_them(self):
        for i in range(5):self.call('developer',f'executor-{i}')
        with self.assertRaises(team.ReviewReserveReached):self.call('developer','executor-6')
        for i in range(3):self.call('reviewer',f'agent-reviewer-0-{i}')
        with self.assertRaises(team.TeamLimitExceeded):self.call('reviewer','agent-reviewer-0-4')
        self.assertEqual(team.count(self.state['run_id']),8)
    def save(self,**changes):
        self.state.update(changes);store.create_run(self.state['run_id'],'synthetic',self.state,'2026-11-09')
    def test_money_reservation_protects_run_and_global_remaining(self):
        self.save(run_budget_cny=1)
        with self.assertRaises(team.ReviewReserveReached):model._reserve(self.state['run_id'],'executor-1','fixture','deepseek-flash',99,.3)
        model._reserve(self.state['run_id'],'agent-reviewer-0-1','fixture','deepseek-flash',99,.3)
        with model._db_connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM model_usage').fetchone()[0],1)
        other={**self.state,'run_id':str(uuid.uuid4()),'run_budget_cny':5};store.create_run(other['run_id'],'synthetic',other,'2026-11-09')
        with self.assertRaises(team.ReviewReserveReached):model._reserve(other['run_id'],'executor-1','fixture','deepseek-flash',1.5,.3)
    def test_model_call_slots_are_also_reserved(self):
        self.save(model_call_limit=3)
        with self.assertRaises(team.ReviewReserveReached):model._reserve(self.state['run_id'],'executor-1','fixture','deepseek-flash',99,.25)
        model._reserve(self.state['run_id'],'agent-reviewer-0-1','fixture','deepseek-flash',99,.25)
    def test_known_preflight_reservation_does_not_leave_uncertain_request(self):
        provider=Mock(side_effect=team.ReviewReserveReached('protected'))
        with self.assertRaises(team.ReviewReserveReached):team.invoke(self.state,'developer','executor-1','s','u',team.SpecialistDecision,100,provider)
        self.assertEqual(team.count(self.state['run_id']),0)
    def test_old_tasks_keep_unreserved_limits(self):
        self.state.pop('team_policy_version')
        for i in range(8):self.call('developer',f'executor-{i}')
        self.assertEqual(team.count(self.state['run_id']),8)
    def test_issue_id_and_evidence_survive_repair_and_replay(self):
        first=team_policy.reconcile(self.state,self.report,self.basis)
        solved={**self.report,'verdict':'ready_for_manual','criteria':[{**self.report['criteria'][0],'status':'supported','evidence':'新增操作已补齐并复查'}]}
        state={**self.state,**first,'repair_round':1,'executor_tasks':[{'changed_paths':['src/App.tsx']}]}
        second=team_policy.reconcile(state,solved,{**self.basis,'source_digest':'b'})
        self.assertEqual(second['review_issues'][0]['id'],'criterion-1');self.assertEqual(second['review_issues'][0]['status'],'resolved')
        self.assertEqual(len(second['review_issues'][0]['events']),2)
        self.assertEqual(second['review_issues'][0]['events'][-1]['changed_paths'],['src/App.tsx'])
        replay=team_policy.reconcile({**state,**second},solved,{**self.basis,'source_digest':'b'})
        self.assertEqual(replay,second)
    def test_manual_does_not_erase_previous_gap(self):
        first=team_policy.reconcile(self.state,self.report,self.basis)
        manual={**self.report,'criteria':[{**self.report['criteria'][0],'status':'manual'}]}
        result=team_policy.reconcile({**self.state,**first,'repair_round':1},manual,{**self.basis,'source_digest':'b'})
        self.assertEqual(result['review_issues'][0]['status'],'open')
    def test_no_change_and_repeated_issue_stop_before_limit(self):
        first=team_policy.reconcile(self.state,self.report,self.basis)
        self.assertTrue(team_policy.no_change({**self.state,**first},self.basis,{}))
        second=team_policy.reconcile({**self.state,**first,'repair_round':1},self.report,self.basis)
        self.assertIn('没有变化',second['review_stop_reason'])
        second=team_policy.reconcile({**self.state,**first,'repair_round':1},self.report,{**self.basis,'source_digest':'b'})
        third=team_policy.reconcile({**self.state,**second,'repair_round':2},self.report,{**self.basis,'source_digest':'c'})
        self.assertIn('连续三轮',third['review_stop_reason'])
    def test_previous_issue_requires_current_support_before_ready(self):
        first=team_policy.reconcile(self.state,self.report,self.basis)
        state={**self.state,**first,'acceptance_criteria':['功能甲'],'repair_round':1}
        context={'basis':{**self.basis,'source_digest':'b'},'manifest':{'files':{'src/App.tsx':{'sha256':'current'}}},'technical':dict.fromkeys(('build_passed','test_passed','health_passed','environment_verified'),True)}
        reads=[{'operation_id':'reviewer_1_1','tool_name':'project.read_file','result':{'ok':True,'data':{'path':'src/App.tsx','sha256':'current','content':'new implementation'}}}]
        report=team.ReviewReport(verdict='ready_for_manual',summary='读取当前源码并复查',criteria=[team.CriterionReview(index=0,status='manual',evidence='这条要求暂时无法确认')])
        with patch('app.tools.history',return_value=reads):
            with self.assertRaisesRegex(runner.SandboxError,'缺口未解决'):team.validate_review(state,report,context,1)
            report.criteria[0]=team.CriterionReview(index=0,status='supported',evidence='已读取新的实现和修复位置',files=['src/App.tsx'])
            team.validate_review(state,report,context,1)
            result=team_policy.reconcile(state,report.model_dump(),context['basis'],context['technical'])
            self.assertEqual(result['review_issues'][0]['status'],'resolved')
            context['basis']=self.basis
            with self.assertRaisesRegex(runner.SandboxError,'没有新的'):team.validate_review({**state,'review_history':[{**first['review_history'][0],'technical':context['technical']}]},report,context,1)
    def test_environment_loss_does_not_call_provider(self):
        provider=Mock()
        with patch.object(team,'context',return_value={'basis':{'environment':None}}):
            with self.assertRaises(team.EnvironmentUnavailable):team.step(self.state,'reviewer',provider)
        provider.assert_not_called()

class FailureExportTests(unittest.TestCase):
    def test_real_source_export_survives_unavailable_docker(self):
        source=runner.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();shutil.copytree(source/'templates',root/'templates')
            patches=[patch.object(runner,'ROOT',root),patch.object(runner,'GENERATED_ROOT',root/'generated-projects'),patch.object(runner,'OUTPUT_ROOT',root/'outputs'),patch.object(store,'DATA_DIR',root/'data'),patch.object(store,'RUNS_DB',root/'data/runs.sqlite3'),patch.object(model,'DATA_DIR',root/'data'),patch.object(model,'USAGE_DB',root/'data/usage.sqlite3'),patch.object(runtime_identity,'current',return_value=None)]
            from contextlib import ExitStack
            with ExitStack() as stack:
                for item in patches:stack.enter_context(item)
                identifier=str(uuid.uuid4());state={'run_id':identifier,'orchestration_version':1,'team_policy_version':1,'runtime_version':3,'product_spec':{'product_name':'合成故障','summary':'离线环境故障'},'acceptance_criteria':['可显示页面'],'review_status':'in_progress','status':'running','build_status':'failed','build_output':'环境不可用','trace':[]}
                store.create_run(identifier,'synthetic',state,'2026-11-09');runner.initialize_workspace(identifier)
                flow=graph.build_graph(root/'data/checkpoints.sqlite3');config={'configurable':{'thread_id':identifier},'recursion_limit':128}
                flow.update_state(config,state,as_node='build')
                try:
                    with patch.object(graph,'generate_json',side_effect=AssertionError('no provider')):result=flow.invoke(None,config)
                finally:flow.checkpointer.conn.close()
                self.assertEqual(result['status'],'failed_validation');self.assertEqual(result['review_status'],'stopped')
                with zipfile.ZipFile(root/result['artifact_paths']['source_zip']) as archive:
                    self.assertIn('src/App.tsx',archive.namelist());evidence=json.loads(archive.read('ACCEPTANCE_EVIDENCE.json'))
                    self.assertFalse(evidence['technical']['environment_verified']);self.assertFalse(evidence['technical']['test_passed'])
                self.assertIn('无法核对',(root/result['artifact_paths']['report']).read_text())
                self.assertEqual(json.loads((root/result['artifact_paths']['trace']).read_text())[-1]['stage'],'delivery')
                from app import main
                store.update_run(identifier,{**result,'review_status':'in_progress'})
                with patch.object(main,'workflow',None):main._record_failure(identifier,model.ModelCallFailed('synthetic interruption'))
                failed=store.get_run(identifier)
                self.assertEqual(failed['status'],'failed_model');self.assertEqual(failed['review_status'],'stopped')
                self.assertFalse(failed['validation_ok']);self.assertTrue((root/failed['artifact_paths']['source_zip']).is_file())

if __name__=='__main__':unittest.main()
