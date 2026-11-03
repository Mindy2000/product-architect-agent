"""Offline regression checks: no Docker, network, or paid model requests."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
from app import graph as module
from app.model import ModelCallFailed


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.records = {}
        self.model_calls = []
        self.repairs = []
        self.pause_requested = False
        self.diagnosis_action = "repair"
        self.fail_repair = False
        self.build_ok = False
        self.config = {'configurable': {'thread_id': 'offline-test'}}
        self.initial = {'run_id': 'offline-test', 'user_request': '离线工作流验收需求', 'repair_round': 0, 'trace': []}
        def model(run_id, stage, *args, **kwargs):
            self.model_calls.append(stage)
            if stage == 'requirements':
                return SimpleNamespace(acceptance_criteria=['验证'], model_dump=lambda: {'product_name': '离线样例'}), {}
            if stage == 'diagnosis':
                return SimpleNamespace(action=self.diagnosis_action, reason='失败决策', model_dump=lambda: {'action':self.diagnosis_action,'reason':'失败决策'}), {}
            if stage in ('planning','replanning'):
                return SimpleNamespace(steps=[SimpleNamespace(title='实现', acceptance_indices=[], model_dump=lambda: {'title':'实现','description':'实现示例','depends_on':[],'acceptance_indices':[]})]), {}
            if stage.startswith('repair-'):
                self.repairs.append(stage)
                if self.fail_repair:
                    raise ModelCallFailed('模拟模型格式错误')
            return SimpleNamespace(files=[], summary='离线生成'), {}
        patches = [
            patch.object(module, 'get_pause_requested', lambda rid: self.pause_requested),
            patch.object(module, 'get_run', lambda rid: self.records.get(rid)),
            patch.object(module, 'update_run', lambda rid, state: self.records.update({rid: state.copy()})),
            patch.object(module, 'generate_json', model),
            patch('app.fullstack.ensure_image', lambda mode: 'cached'),
            patch.object(module, 'initialize_workspace', lambda rid, parent=None: Path(module.__file__).resolve().parents[3] / 'generated-projects' / rid),
            patch.object(module, 'apply_generated_files', lambda *a: None),
            patch.object(module, 'read_generated_sources', lambda rid: {}),
            patch.object(module, 'run_phase', lambda rid, phase, **kw: SimpleNamespace(exit_code=0 if self.build_ok else 1, output='模拟构建结果', duration_seconds=0)),
            patch('app.runner.capture_preview', lambda rid: SimpleNamespace(exit_code=0,output='preview')),
            patch.object(module, 'deliver', lambda rid, state: {'trace': str(self.root / 'trace.json')}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.workflow = module.build_graph(self.root / 'checkpoints.sqlite3')
        self.addCleanup(self.workflow.checkpointer.conn.close)

    def test_failed_build_stops_after_three_repairs_and_delivers(self):
        result = self.workflow.invoke(self.initial, self.config)
        self.assertEqual(self.repairs, ['repair-1', 'repair-2', 'repair-3'])
        self.assertEqual(result['repair_round'], 3)
        self.assertEqual(result['status'], 'failed_validation')
        self.assertEqual(self.records['offline-test']['repair_round'], 3)

    def test_failed_repair_resume_counts_attempt_across_recreated_graph(self):
        self.fail_repair = True
        with self.assertRaises(ModelCallFailed):
            self.workflow.invoke(self.initial, self.config)
        self.assertEqual(self.records['offline-test']['repair_round'], 1)
        self.workflow = module.build_graph(self.root / 'checkpoints.sqlite3')
        self.addCleanup(self.workflow.checkpointer.conn.close)
        self.fail_repair = False
        result = self.workflow.invoke(None, self.config)
        self.assertEqual(self.repairs, ['repair-1', 'repair-2', 'repair-3'])
        self.assertEqual(result['repair_round'], 3)
        self.assertEqual(result['status'], 'failed_validation')

    def test_pause_and_resume_checkpoint_without_regeneration(self):
        from langgraph.types import Command
        self.build_ok = True
        def phase(rid, phase, **kwargs):
            if phase == "build": self.pause_requested = True
            return SimpleNamespace(exit_code=0,output="passed",duration_seconds=0)
        with patch.object(module,"run_phase",phase):
            result = self.workflow.invoke(self.initial, self.config)
        self.assertIn('__interrupt__', result)
        self.assertEqual(self.records['offline-test']['status'], 'paused')
        self.pause_requested = False
        result = self.workflow.invoke(Command(resume=True), self.config)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.repairs, [])
        self.assertEqual(self.model_calls, ['requirements','planning','generation'])

    def test_cancel_before_start_does_not_call_models(self):
        from app.store import CancellationRequested
        with patch.object(module,'cancellation',return_value=True):
            with self.assertRaises(CancellationRequested):self.workflow.invoke(self.initial,self.config)
        self.assertEqual(self.model_calls,[])

    def test_restore_build_validation_never_calls_models(self):
        state={**self.initial,'repair_limit':0}
        self.workflow.update_state(self.config,state,as_node='generation')
        result=self.workflow.invoke(None,self.config)
        self.assertEqual(result['status'],'failed_validation')
        self.assertEqual(self.model_calls,[])

    def test_agent_can_replan_but_replanning_is_bounded(self):
        self.diagnosis_action='replan'
        result=self.workflow.invoke(self.initial,self.config)
        self.assertEqual(result['status'],'failed_validation')
        self.assertEqual(result['repair_round'],3)
        self.assertEqual(result['replan_count'],2)
        self.assertEqual(self.model_calls.count('replanning'),2)

    def test_agent_can_stop_when_task_is_not_repairable(self):
        self.diagnosis_action='stop'
        result=self.workflow.invoke(self.initial,self.config)
        self.assertEqual(result['status'],'failed_validation')
        self.assertEqual(self.repairs,[])

    def test_successful_build_and_browser_validation_deliver_without_repair(self):
        self.build_ok = True
        result = self.workflow.invoke(self.initial, self.config)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.repairs, [])


if __name__ == '__main__':
    unittest.main()
