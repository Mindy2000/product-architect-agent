"""Offline checks of frozen contracts, protected execution, and stale evidence."""
import copy
import unittest
from unittest.mock import patch
import test_closeout
from app import interface_checks as checks, page_health, store, runner, tools, project, acceptance, action_protocol

CRITERIA = ['输入标签任务标题，按钮添加任务；ul的aria-label为任务列表',
            '数据只在localStorage，刷新后保留；每行有删除+标题按钮，删除后刷新仍不存在']


class InterfaceChecks(unittest.TestCase):
    setUp = test_closeout.AcceptanceTests.setUp
    receipt = test_closeout.AcceptanceTests.receipt

    def configure(self):
        self.state.update(requirements_confirmed=True, acceptance_criteria=copy.deepcopy(CRITERIA),
                          product_spec={'application_type':'frontend','page_details':[{'route':'/'}]})
        store.update_run(self.id,self.state)

    def test_explicit_contract_is_protected_and_does_not_confirm_requirements(self):
        self.configure()
        self.assertEqual(checks.contract(self.id)['checks'][0]['kind'],'local_task_delete')
        with self.assertRaises(project.ProjectError):
            project.apply_changes(self.id,[project.FileChange(operation='create',path='tests/standard/health.spec.ts',content='skip')],'forbidden')
        self.receipt('project.build');self.receipt('project.test')
        value=acceptance.matrix(self.id)
        self.assertEqual(value['counts']['passed'],0)
        self.assertTrue(value['coverage']['interface_contract']['manual_required'])

    def test_unconfirmed_or_multiroute_does_not_guess(self):
        self.configure();self.state['requirements_confirmed']=False;store.update_run(self.id,self.state)
        self.assertEqual(checks.script(self.id),'')
        self.state['requirements_confirmed']=True;self.state['product_spec']['page_details'].append({'route':'/tasks'})
        store.update_run(self.id,self.state);self.assertEqual(checks.script(self.id),'')

    def test_negation_or_document_text_does_not_create_a_contract(self):
        self.configure();self.state['acceptance_criteria'][1]=self.state['acceptance_criteria'][1].replace('每行有','禁止每行有')
        self.state['knowledge_context']={'text':CRITERIA}
        store.update_run(self.id,self.state);self.assertEqual(checks.contract(self.id)['checks'],[])

    def test_unmatched_projects_keep_original_health_fingerprint(self):
        before=page_health.fingerprint(self.id)
        self.state['acceptance_criteria']=['别的未知约定'];store.update_run(self.id,self.state)
        self.assertEqual(page_health.fingerprint(self.id),before)
        self.assertNotIn('interface_contract',page_health.coverage(self.id))

    def test_changed_contract_invalidates_old_receipt_and_basis(self):
        before=acceptance.matrix(self.id)['basis_hash'];self.receipt('project.build');self.receipt('project.test')
        self.configure()
        value=acceptance.matrix(self.id)
        self.assertNotEqual(value['basis_hash'],before)
        self.assertFalse(value['technical']['health_passed'])
        self.assertEqual(value['review_status'],'pending')

    def test_crud_requires_matching_schema_and_explicit_done_contract(self):
        self.configure();self.state['product_spec'].update(application_type='fullstack_crud',crud_schema={'entities':[{'name':'tasks','fields':[{'name':'title','type':'text'},{'name':'done','type':'boolean'}]}]})
        self.state['acceptance_criteria']=['标签任务标题的输入框和新增任务按钮；aria-label=任务列表的ul/li表示真实记录；checkbox标签完成+任务标题；新增默认done=false，更新与完成状态刷新后保留']
        store.update_run(self.id,self.state)
        self.assertEqual(checks.contract(self.id)['checks'][0]['kind'],'crud_task_toggle')
        self.state['product_spec']['crud_schema']['entities'][0]['fields'][1]['type']='text'
        store.update_run(self.id,self.state);self.assertEqual(checks.contract(self.id)['checks'],[])

    def test_declared_text_is_never_executable_javascript(self):
        self.configure();self.state['acceptance_criteria'].append("'); throw new Error('injection');//")
        store.update_run(self.id,self.state)
        self.assertNotIn('injection',checks.script(self.id))

    def test_independent_failure_overrides_passing_generated_tests(self):
        self.configure();self.receipt('project.build')
        success=runner.CommandResult(0,'generated passed',.1)
        failure=runner.CommandResult(1,'expected exact 删除独立控件甲',.1)
        with patch.object(runner,'run_phase',side_effect=[success,failure]) as run:
            result=tools._test(tools.ToolContext(self.id,'quality','contract'),None)
        self.assertFalse(result['passed']);self.assertFalse(result['health_passed'])
        self.assertIn('删除独立控件甲',result['output'])
        self.assertEqual([x.args[1] for x in run.call_args_list],['test','health'])

    def test_old_cached_success_cannot_skip_contract_execution(self):
        self.receipt('project.build');self.receipt('project.test');self.configure()
        with patch.object(runner,'run_phase',return_value=runner.CommandResult(0,'passed',.1)) as run:
            result=tools._test(tools.ToolContext(self.id,'quality','new-contract'),None)
        self.assertEqual(run.call_count,2);self.assertTrue(result['passed'])

    def test_developer_and_reviewer_receive_compiled_contract_and_limits(self):
        self.configure();value=action_protocol.interface_contract(self.state)
        self.assertTrue(value['fixed_interface_checks']['checks'])
        self.assertIn('失败回滚',value['policy'])
