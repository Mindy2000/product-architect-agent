"""Synthetic UX contracts; no provider, OCR rerun or private project access."""
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import source_mapping, source_ast, rule_analysis, document_quality, library, workbench, model
from app.visual_policy import VisualPolicy
from pydantic import ValidationError
from test_workbench import Fixture as WorkbenchFixture


class SourceTests(unittest.TestCase):
    def test_ast_finds_jsx_component_without_matching_comment(self):
        files = {'src/App.tsx': '// 标题\nexport function Card(){return <h1>标题</h1>}'}
        result = source_mapping.associate(files, {'text': '标题'})
        if not result['parser']['available']: self.skipTest('Optional local parser unavailable')
        self.assertEqual(result['locations'][0]['line'], 2)
        self.assertEqual(result['locations'][0]['component'], 'Card')
        self.assertEqual(result['locations'][0]['basis'], 'jsx_text')
        self.assertTrue(result['uncertain'])

    def test_marker_in_comment_is_not_verified(self):
        result = source_mapping.associate({'src/App.tsx': '// data-pa-source="src/App.tsx:1"\nexport const a=1'}, {'source_marker': 'src/App.tsx:1'})
        self.assertTrue(result['uncertain'])
        self.assertFalse(any(v['basis']=='explicit_marker' for v in result['locations']))

    def test_parser_never_executes_supplied_source(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'must-not-exist'
            source = 'require("node:fs").writeFileSync('+json.dumps(str(marker))+',"bad");export default function App(){return <p>合成</p>}'
            result = source_mapping.associate({'src/App.tsx': source}, {'text': '合成'})
            self.assertFalse(marker.exists())
            self.assertTrue(result['locations'])

    def test_missing_parser_explicitly_returns_uncertain_fallback(self):
        with patch.object(source_ast.shutil, 'which', return_value=None):
            result = source_mapping.associate({'src/App.tsx':'export default ()=><b>标题</b>'}, {'text':'标题'})
        self.assertFalse(result['parser']['available'])
        self.assertTrue(result['uncertain'])
        self.assertEqual(result['locations'][0]['basis'], 'text_match')


class RuleTests(unittest.TestCase):
    def rule(self, rid, scope, target, value, priority=0):
        return dict(id=rid, title=rid, content=rid, scope_type=scope, targets=target, constraints={'color':value}, priority=priority)

    def test_separate_pages_can_have_different_values(self):
        self.assertEqual(rule_analysis.analyze([self.rule('a','page',['/a'],'blue'),self.rule('b','page',['/b'],'red')]), [])

    def test_general_and_local_conflict_is_not_silently_overridden(self):
        rules=[self.rule('a','project',[],'blue'),self.rule('b','page',['/a'],'red',100)]
        before=json.dumps(rules)
        result=rule_analysis.analyze(rules)
        self.assertEqual(result[0]['kind'],'conflict')
        self.assertEqual(result[0]['priorities'],[0,100])
        self.assertIn('不自动覆盖',result[0]['resolution'])
        self.assertEqual(json.dumps(rules),before)

    def test_cross_type_overlap_remains_cautious_and_equal_values_redundant(self):
        rules=[self.rule('a','page',['/a'],'blue'),self.rule('b','task',['task-1'],'red')]
        self.assertIn('可能',rule_analysis.analyze(rules)[0]['scope'])
        rules[1]['constraints']['color']='blue'
        self.assertEqual(rule_analysis.analyze(rules)[0]['kind'],'redundant')

    def test_older_client_edit_preserves_priority_and_freeze(self):
        from test_interactive_expansion import ExpansionTests
        from app import collaboration as c
        fixture=ExpansionTests();fixture.setUp()
        try:
            rid=uuid.UUID(fixture.rid)
            rule=c.add_rule(rid,c.RuleInput(title='配色',content='蓝色',priority=70))
            edited=c.edit_rule(rid,uuid.UUID(rule['id']),c.RuleInput(title='配色',content='深蓝色',revision=1))
            self.assertEqual(edited['priority'],70)
            self.assertEqual(c.freeze_rules(fixture.rid)[0]['priority'],70)
        finally:fixture.tearDown()


class QualityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        mocked=patch.object(library,'DATA',Path(temporary.name));mocked.start();self.addCleanup(mocked.stop)
        self.cid=str(uuid.uuid4());self.did=str(uuid.uuid4());self.chunk=str(uuid.uuid4());now=library.now()
        self.content='产品规则：姓名必填，重复报名应拒绝。'
        with closing(library.connect()) as conn:
            conn.execute('INSERT INTO collections VALUES (?,?,?,?)',(self.cid,'合成规则','',now))
            conn.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.did,self.cid,'合成规则.txt',64,'doc-sha','ready',100,'','[]',1,1,'job',now,now))
            conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)',(self.chunk,self.did,self.cid,0,None,1,1,self.content,0,len(self.content)))
            conn.execute('INSERT INTO chunk_search VALUES (?,?)',(self.chunk,' '.join(library.tokenize(self.content))));conn.commit()

    def test_empty_ocr_replacement_and_repeated_text_are_warnings_not_deletions(self):
        pages=[{'text':'','method':'local_ocr'},{'text':'abc\ufffd'},{'text':'abc\ufffd'}]
        result=document_quality.assess(pages)
        self.assertEqual(result['empty_units'],1);self.assertEqual(result['duplicate_units'],1)
        self.assertGreaterEqual(len(result['warnings']),4)
        self.assertEqual(len(pages),3)

    def test_preview_has_bounded_quality_and_immutable_document_fingerprint(self):
        result=library.preview_chunks(uuid.UUID(self.did))
        self.assertEqual(result['quality']['document_sha256'],'doc-sha')
        self.assertTrue(result['quality']['sample_complete'])
        self.assertEqual(result['quality']['provider_requests'],0)

    def test_retrieval_has_content_binding_and_no_hit_is_not_service_error(self):
        hit=library.search(library.SearchInput(collection_ids=[self.cid],query='姓名必填'),record=False)
        self.assertEqual(hit['outcome'],'matched')
        self.assertEqual(hit['chunks'][0]['source_chunk_sha256'],hashlib.sha256(self.content.encode()).hexdigest())
        miss=library.search(library.SearchInput(collection_ids=[self.cid],query='zzzzunknown'),record=False)
        self.assertEqual(miss['outcome'],'no_results');self.assertEqual(miss['status'],'ok')
        self.assertIn('未自动切换',miss['message'])

    def test_citation_verifies_body_and_chunk_ownership(self):
        item={'chunk_id':self.chunk,'document_id':self.did,'document_sha256':'doc-sha','content':self.content[:8]}
        self.assertEqual(library.check_citations(library.CitationCheck(chunks=[item]))['items'][0]['content_status'],'prefix_verified')
        forged={**item,'content':'不存在的引用'}
        self.assertEqual(library.check_citations(library.CitationCheck(chunks=[forged]))['items'][0]['status'],'changed')
        missing={**item,'chunk_id':'missing'}
        self.assertEqual(library.check_citations(library.CitationCheck(chunks=[missing]))['items'][0]['status'],'chunk_missing')

    def test_remote_citation_never_requeries_provider(self):
        with patch.object(model,'_client',side_effect=AssertionError('provider forbidden')):
            result=library.check_citations(library.CitationCheck(chunks=[{'provider_id':'dify_cloud','chunk_id':'remote'}]))
        self.assertEqual(result['items'][0]['status'],'remote_snapshot')

    def test_malformed_citation_metadata_returns_explicit_status(self):
        for item in [{'source':[],'chunk_id':'bad'}, {'document_id':[], 'chunk_id':{}}, {'document_id':self.did,'chunk_id':[]}]:
            result=library.check_citations(library.CitationCheck(chunks=[item]))
            self.assertEqual(result['items'][0]['status'],'invalid_metadata')


class SetupTests(WorkbenchFixture):
    def test_model_save_preserves_credentials_and_does_not_call_provider(self):
        with patch.object(model,'_client',side_effect=AssertionError('provider forbidden')):
            result=workbench.save_setup(workbench.SetupConfig(provider='deepseek',model_name='deepseek-v4-pro'))
        self.assertEqual(result['provider_requests'],0)
        env=(self.root/'.env').read_text()
        self.assertIn(self.secret,env);self.assertIn('MODEL_NAME=deepseek-v4-pro',env)

    def test_optional_service_cannot_enable_text_model_and_status_has_no_secrets(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):workbench.save_setup(workbench.SetupConfig(provider='qwen',model_name='deepseek-flash'))
        with patch.object(workbench,'local_command',return_value=False):status=workbench.setup_status()
        self.assertNotIn(self.secret,json.dumps(status));self.assertTrue(status['service_keys']['deepseek'])
        self.assertIn('未联网',status['credential_notice'])

    def test_nan_budget_remains_serializable_and_not_ready(self):
        (self.root/'.env').write_text('MODEL_BUDGET_CNY=nan\n')
        with patch.object(workbench,'local_command',return_value=False):status=workbench.setup_status()
        json.dumps(status,allow_nan=False)
        self.assertFalse(next(c for c in status['checks'] if c['id']=='budget')['ready'])


class VisualTests(unittest.TestCase):
    def test_rectangle_cannot_escape_viewport_or_be_used_in_live_mode(self):
        for value in [dict(enabled=True,viewport='mobile',masks=[dict(x=380,y=0,width=20,height=20)]),
                      dict(enabled=False,region=dict(x=0,y=0,width=20,height=20))]:
            with self.assertRaises(ValidationError):VisualPolicy.model_validate(value)


if __name__=='__main__':unittest.main()
