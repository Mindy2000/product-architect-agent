"""No providers: bounded evidence allocation and final review rejection semantics."""
import json
import unittest
from unittest.mock import patch
import test_remaining_closeout
from app import review_context, team, model, tools, runner


class EvidenceAllocationTests(unittest.TestCase):
    receipt=test_remaining_closeout.RetainedEvidenceTests.receipt

    def test_format_feedback_matches_remaining_tool_permissions(self):
        early=review_context.format_feedback(3)
        self.assertIn('tool',early['allowed_actions']);self.assertIn('arguments',early['required_tool_fields'])
        for remaining in [2,1,0]:
            final=review_context.format_feedback(remaining)
            self.assertEqual(final['allowed_actions'],['submit','stop'])
            self.assertNotIn('required_tool_fields',final)
            self.assertEqual(final['criteria_location'],'review.criteria')

    def fixture(self):
        bodies={'src/App.tsx':'a'*9679,'tests/app.spec.ts':'t'*3452,
                'src/pa-crud-client.ts':'c'*2357,'src/style.css':'s'*1871,
                'backend/schema.json':'j'*385,'backend/file_store.py':'f'*9987,
                'backend/runtime.py':'r'*10386}
        manifest={'files':{p:{'sha256':'current'} for p in bodies if not p.startswith('backend/')},
                  'framework':{p:'current' for p in bodies if p.startswith('backend/')}}
        history=[self.receipt(p,b,turn=i+1) for i,(p,b) in enumerate(bodies.items())]
        return bodies,manifest,history

    def test_long_backends_do_not_evict_small_application_evidence(self):
        bodies,m,h=self.fixture();packet=review_context.retained_source_evidence(m,h,0)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),22000)
        by_path={f['path']:f for f in packet['files']}
        for path in ['src/App.tsx','tests/app.spec.ts','src/pa-crud-client.ts','src/style.css','backend/schema.json']:
            self.assertTrue(by_path[path]['full_file_present']);self.assertEqual(by_path[path]['segments'][0]['content'],bodies[path])
        for path in ['backend/file_store.py','backend/runtime.py']:
            self.assertFalse(by_path[path]['full_file_present']);self.assertGreater(len(by_path[path]['segments'][0]['content']),0)

    def test_escaping_and_many_files_stay_within_serialized_budget(self):
        bodies={f'src/component{i}.tsx':'"中文\\\n'*5000 for i in range(40)}
        m={'files':{p:{'sha256':'current'} for p in bodies}}
        h=[self.receipt(p,b,turn=i+1) for i,(p,b) in enumerate(bodies.items())]
        packet=review_context.retained_source_evidence(m,h,0,budget=3000)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),3000)
        self.assertGreater(packet['omitted_files'],0)
        for f in packet['files']:
            self.assertFalse(f['full_file_present'])
            for seg in f['segments']:self.assertEqual(seg['content'],bodies[f['path']][seg['start']:seg['end']])

    def test_partial_ranges_and_omissions_are_explicit_not_inferred(self):
        bodies,m,h=self.fixture();packet=review_context.retained_source_evidence(m,h,0,budget=1600)
        ctx={'manifest':m,'source_evidence':packet,'technical':{}}
        contract=review_context.submission_contract(ctx,{'acceptance_criteria':['一','二']},h)
        self.assertEqual(contract['required_indices'],[0,1])
        self.assertEqual(set(contract['citable_files'])|set(contract['read_but_omitted_files']),set(bodies))
        self.assertTrue(contract['partial_files']);self.assertFalse(contract['available_fixed_checks'])
        self.assertFalse(contract['ready_requires_current_technical_checks'])

    def test_stale_reads_and_other_episodes_cannot_be_cited(self):
        _,m,h=self.fixture();h[0]['result']['data']['sha256']='stale';h[1]['operation_id']='reviewer_1_2'
        ctx={'manifest':m,'source_evidence':review_context.retained_source_evidence(m,h,0),'technical':{}}
        c=review_context.submission_contract(ctx,{},h)
        self.assertNotIn('src/App.tsx',c['citable_files']);self.assertNotIn('tests/app.spec.ts',c['citable_files'])

    def test_final_schema_and_parser_reject_arrays_without_executing_tools(self):
        self.assertEqual(team.FinalReviewerAction.model_json_schema()['type'],'object')
        stop={'action':'stop','summary':'证据不足保留问题'}
        with patch.object(tools,'execute') as execute:
            for raw in [json.dumps([stop]),json.dumps([stop,stop]),json.dumps(json.dumps(stop)),json.dumps(stop)+json.dumps(stop)]:
                with self.assertRaises(ValueError):model.parse_model_json(raw,team.FinalReviewerAction)
            execute.assert_not_called()
        self.assertEqual(model.parse_model_json(json.dumps(stop),team.FinalReviewerAction).action,'stop')

    def test_final_feedback_distinguishes_omitted_from_unread_without_read_instruction(self):
        _,m,h=self.fixture()
        state={'run_id':'synthetic','agent_protocol_version':3,'acceptance_criteria':['界面样式']}
        ctx={'manifest':m,'technical':{},'source_evidence':{'files':[]},'step_budget':{'role_remaining':1}}
        report=team.EvidenceReviewReport(verdict='blocked',summary='缺少当前可用正文',criteria=[team.EvidenceCriterionReview(index=0,status='supported',evidence='已经读取样式正文',files=['src/style.css'])])
        with patch.object(tools,'history',return_value=h),self.assertRaises(team.ReviewEvidenceError) as caught:
            team.validate_review(state,report,ctx,0)
        d=caught.exception.details
        self.assertEqual(d['criteria'][0]['unread_files'],[]);self.assertEqual(d['criteria'][0]['omitted_files'],['src/style.css'])
        self.assertEqual(d['read_next'],[]);self.assertIsNone(d['allowed_tool'])
        self.assertIn('当前只允许提交或停止',str(caught.exception));self.assertNotIn('先用project.read_file',str(caught.exception))

    def test_earlier_feedback_can_request_actual_missing_read(self):
        _,m,_=self.fixture()
        state={'run_id':'synthetic','agent_protocol_version':3,'acceptance_criteria':['界面样式']}
        ctx={'manifest':m,'technical':{},'source_evidence':{'files':[]},'step_budget':{'role_remaining':3}}
        report=team.EvidenceReviewReport(verdict='blocked',summary='缺少当前可用正文',criteria=[team.EvidenceCriterionReview(index=0,status='supported',evidence='需要样式正文核对',files=['src/style.css'])])
        with patch.object(tools,'history',return_value=[]),self.assertRaises(team.ReviewEvidenceError) as caught:
            team.validate_review(state,report,ctx,0)
        self.assertEqual(caught.exception.details['read_next'],['src/style.css'])
        self.assertEqual(caught.exception.details['allowed_tool'],'project.read_file')
