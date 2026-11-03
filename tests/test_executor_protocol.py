"""Focused regressions for the observed executor failure; all state is temporary."""
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import get_args
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from pydantic import ValidationError
from app import executor,model,project,runner,tools,store,designs


class ProtocolTests(unittest.TestCase):
    def decision(self,**changes):
        return {'action':'tool','task_id':'task-3','tool':'project.read_file','arguments':{'path':'src/App.tsx'},'summary':'读取当前文件',**changes}

    def test_saved_rejections_now_parse_without_changing_tool_or_arguments(self):
        data=json.loads((Path(__file__).parent/'fixtures/executor_action_failures.json').read_text())
        self.assertTrue(data['records'])
        for record in data['records']:
            with self.subTest(stage=record['stage']):
                original=record['decision'];parsed=model.parse_model_json(json.dumps(original),model.ExecutorDecision)
                self.assertEqual(parsed.action,'tool')
                self.assertEqual(parsed.tool,original['tool'])
                self.assertEqual(parsed.task_id,original['task_id'])
                self.assertEqual(parsed.arguments,original['arguments'])
                self.assertEqual(original['action'],original['tool'])

    def test_tool_types_and_registry_agree(self):
        self.assertEqual(set(get_args(model.ExecutorTool)),set(tools.TOOLS))

    def test_plain_and_whole_fenced_json_preserve_arguments(self):
        original=self.decision(arguments={'path':'src/App.tsx','offset':0,'length':20})
        raw=json.dumps(original)
        for value in (raw,'```json\n'+raw+'\n```','```\n'+raw+'\n```'):
            self.assertEqual(model.parse_model_json(value,model.ExecutorDecision).arguments,original['arguments'])

    def test_embedded_or_broken_json_is_not_repaired_or_executed(self):
        for raw in ('Explanation '+json.dumps(self.decision()),'```json\n{broken\n```','{"action":"tool"'):
            with self.subTest(raw=raw),self.assertRaises((ValueError,ValidationError)):
                model.parse_model_json(raw,model.ExecutorDecision)

    def test_unknown_conflicting_and_wrong_typed_actions_are_rejected(self):
        for change in ({'action':'shell.exec'},{'action':{}},{'action':'project.read_file','tool':'project.apply_changes'},
                       {'action':'project.read_file','tool':{}},{'task_id':None},
                       {'action':'complete_task'},{'unexpected':'value'}):
            with self.subTest(change=change),self.assertRaises(ValidationError):
                model.ExecutorDecision.model_validate(self.decision(**change))

    def test_alias_does_not_bypass_tool_argument_schema(self):
        action=model.ExecutorDecision.model_validate(self.decision(action='project.read_file',arguments={'path':'src/App.tsx','command':'ls'}))
        with self.assertRaises(ValidationError):tools.ReadArgs.model_validate(action.arguments)


class ExecutorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        patches=[patch.object(runner,'ROOT',self.root),patch.object(project,'manifest',return_value={'digest':'test-digest','files':{}}),
                 patch.object(executor,'_context',return_value={'current_task':{'id':'task-1'}})]
        for item in patches:item.start();self.addCleanup(item.stop)
        self.state={'run_id':str(uuid.uuid4()),'execution_step_limit':4,'executor_turns':0,'executor_tasks':[
            {'id':'task-1','title':'实现任务','description':'','depends_on':[],'status':'pending','successful_tools':0,'changed_paths':[]}]}

    def model_decision(self):
        return model.ExecutorDecision(action='tool',task_id='task-1',tool='project.list_files',arguments={},summary='读取清单')

    def test_ready_decision_is_reused_after_checkpoint_gap_without_second_model_call(self):
        generate=Mock(return_value=(self.model_decision(),{'estimated_cost_cny':0.01}))
        with patch.object(executor,'generate_json',generate):
            first=executor.decide(self.state);second=executor.decide(self.state)
        self.assertEqual(generate.call_count,1)
        self.assertEqual(first['decision'],second['decision'])
        self.assertTrue(second['usage']['replayed'])

    def test_same_operation_is_only_applied_once_on_replayed_checkpoint(self):
        handler=Mock(return_value={'changed_paths':['src/App.tsx']})
        spec=tools.ToolSpec('project.apply_changes','test-only',tools.EmptyArgs,handler,10,True)
        state={**self.state,'pending_executor':{'turn':1,'decision':{
            'action':'tool','task_id':'task-1','tool':'project.apply_changes','arguments':{},'summary':'受控写入'}}}
        with patch.dict(tools.TOOLS,{'project.apply_changes':spec}):
            first=executor.apply_decision(state);second=executor.apply_decision(state)
        self.assertEqual(handler.call_count,1)
        self.assertEqual(first['executor_tasks'],second['executor_tasks'])
        self.assertEqual(second['executor_tasks'][0]['successful_tools'],1)

    def test_two_consecutive_invalid_responses_stop_without_executing_tools(self):
        failure=model.ModelOutputInvalid('invalid response',{'estimated_cost_cny':0.01})
        with patch.object(executor,'generate_json',side_effect=failure) as generate,patch.object(tools,'execute') as execute:
            first=executor.decide(self.state)
            state={**self.state,'pending_executor':first}
            update=executor.apply_decision(state)
            self.assertFalse(update['executor_stopped'])
            state.update(update);state['pending_executor']=executor.decide(state)
            update=executor.apply_decision(state)
        self.assertEqual(generate.call_count,2)
        self.assertTrue(update['executor_stopped'])
        self.assertEqual(update['executor_format_errors'],2)
        execute.assert_not_called()

    def test_successful_decision_resets_consecutive_format_error_counter(self):
        state={**self.state,'executor_format_errors':1,'pending_executor':{'turn':2,'decision':{
            'action':'tool','task_id':'task-1','tool':'project.list_files','arguments':{},'summary':'读取清单'}}}
        with patch.object(tools,'execute',return_value={'ok':True,'data':{}}):update=executor.apply_decision(state)
        self.assertEqual(update['executor_format_errors'],0)
        self.assertFalse(update['executor_stopped'])

    def test_network_failure_is_not_treated_as_a_format_retry(self):
        # The message intentionally contains the old Chinese classifier to catch string-based detection.
        with patch.object(executor,'generate_json',side_effect=model.ModelCallFailed('network 结构校验 marker')) as generate:
            with self.assertRaises(model.ModelCallFailed):executor.decide(self.state)
        self.assertEqual(generate.call_count,1)

    def test_wrong_task_cannot_execute_a_tool(self):
        state={**self.state,'pending_executor':{'turn':1,'decision':{
            'action':'tool','task_id':'task-2','tool':'project.list_files','arguments':{},'summary':'错误任务'}}}
        with patch.object(tools,'execute') as execute:update=executor.apply_decision(state)
        execute.assert_not_called()
        self.assertEqual(update['executor_tasks'][0]['status'],'pending')
        self.assertIn('当前',update['executor_feedback'])

    def test_finish_cannot_skip_pending_tasks_or_final_readiness(self):
        state={**self.state,'pending_executor':{'turn':1,'decision':{'action':'finish','tool':None,'arguments':{},'summary':'结束'}}}
        with patch.object(tools,'_ready') as ready:update=executor.apply_decision(state)
        self.assertFalse(update['executor_done']);ready.assert_not_called()
        state['executor_tasks'][0]['status']='completed'
        with patch.object(tools,'_ready',side_effect=project.ProjectError('缺少测试文件')):
            update=executor.apply_decision(state)
        self.assertFalse(update['executor_done']);self.assertIn('缺少测试',update['executor_feedback'])

    def test_step_limit_stops_before_another_model_request(self):
        self.state['execution_step_limit']=1;self.state['executor_turns']=1
        with patch.object(executor,'generate_json') as generate:pending=executor.decide(self.state)
        generate.assert_not_called();self.assertEqual(pending['decision']['action'],'stop')


class BudgetScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        root=Path(self.directory.name)
        patches=[patch.object(model,'DATA_DIR',root),patch.object(model,'USAGE_DB',root/'usage.sqlite3'),
            patch.object(model,'_get_settings',return_value={'budget_cny':99}),
            patch.object(store,'get_run',return_value={'run_budget_cny':1,'model_call_limit':80}),patch.object(designs,'get_design',return_value=None)]
        for item in patches:item.start();self.addCleanup(item.stop)
        model._connect().close()

    def add(self,run_id,actual,reserve=0.25):
        with model._db_connection() as conn:
            conn.execute('INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),run_id,'test','deepseek','deepseek-flash',reserve,actual,1,1,'completed' if actual is not None else 'uncertain','2026-11-01'))

    def test_task_reports_one_yuan_limit_instead_of_global_ninety_nine(self):
        self.add('target',0.755441)
        result=model.budget_status('target')
        self.assertEqual(result['budget_cny'],1)
        self.assertEqual(result['remaining_cny_estimate'],0.244559)
        self.assertEqual(result['global_budget_cny'],99)
        self.assertEqual(result['scope'],'run')

    def test_available_amount_respects_global_balance_as_well_as_task_balance(self):
        self.add('target',0.1);self.add('other',98.7)
        result=model.budget_status('target')
        self.assertEqual(result['remaining_cny_estimate'],0.9)
        self.assertEqual(result['available_for_new_calls_cny'],0.2)
        self.assertEqual(model.budget_status()['budget_cny'],99)

    def test_uncertain_call_keeps_its_reserve(self):
        self.add('target',None,reserve=0.25)
        self.assertEqual(model.budget_status('target')['spent_cny_estimate'],0.25)

    def test_insufficient_reserve_does_not_insert_or_raise_task_budget(self):
        self.add('target',0.755441)
        with self.assertRaises(model.BudgetExceeded) as caught:model._reserve('target','executor-29','deepseek','deepseek-flash',99,0.25)
        self.assertIn('预留',str(caught.exception));self.assertIn('0.2446',str(caught.exception))
        self.assertEqual(model.budget_status('target')['calls'],1)
        self.assertEqual(model.budget_status('target')['budget_cny'],1)

    def test_global_cap_remains_enforced(self):
        self.add('other',98.9)
        with self.assertRaises(model.BudgetExceeded):model._reserve('target','test','deepseek','deepseek-flash',99,0.25)
        self.assertEqual(model.budget_status()['calls'],1)


if __name__=='__main__':unittest.main()
