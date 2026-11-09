import base64,io,json,uuid,unittest
from unittest.mock import patch
from fastapi import HTTPException
from pydantic import ValidationError
from test_interactive_expansion import ExpansionTests as Fixtures
from app import collaboration as c,collaboration_lifecycle as life,page_feedback as pf,project,store,tools,custom_templates as custom,capability_templates as templates,execution_replay as replay,source_mapping,evaluation
from app.visual_policy import VisualPolicy
class EvolutionTests(unittest.TestCase):
 setUp=Fixtures.setUp
 tearDown=Fixtures.tearDown
 frame=Fixtures.frame
 change_source=Fixtures.change_source
 def note(self):
  with patch.object(pf,'capture',return_value=self.frame()):return pf.add_note(uuid.UUID(self.rid),pf.NoteInput(comment='调整标题的留白'))
 def test_archival_frees_slot_but_frozen_reference_blocks_delete(self):
  n=self.note();uid=uuid.UUID(n['id']);rid=uuid.UUID(self.rid)
  life.archive(rid,'note',uid,life.ArchiveInput(archived=True,revision=1));self.assertEqual(life.search(rid)['items'],[]);self.assertEqual(len(life.search(rid,archived=True)['items']),1)
  child=str(uuid.uuid4());store.create_run(child,'frozen',{'run_id':child,'parent_run_id':self.rid,'selected_annotations':[n]},'now')
  plan=life.preview_delete(rid,'note',uid);self.assertFalse(plan['can_delete'])
  with self.assertRaises(HTTPException):life.delete(rid,'note',uid,life.DeleteInput(token=plan['token'],confirmed=True))
  self.assertTrue((pf.folder(self.rid)/(n['image']+'.png')).exists())
 def test_deletion_requires_fresh_preview_and_removes_only_own_image(self):
  n=self.note();other=self.note();rid=uuid.UUID(self.rid);uid=uuid.UUID(n['id']);plan=life.preview_delete(rid,'note',uid)
  life.archive(rid,'note',uid,life.ArchiveInput(archived=True,revision=1))
  with self.assertRaises(HTTPException):life.delete(rid,'note',uid,life.DeleteInput(token=plan['token'],confirmed=True))
  plan=life.preview_delete(rid,'note',uid);life.delete(rid,'note',uid,life.DeleteInput(token=plan['token'],confirmed=True))
  self.assertFalse((pf.folder(self.rid)/(n['image']+'.png')).exists());self.assertTrue((pf.folder(self.rid)/(other['image']+'.png')).exists())
 def test_archived_comparison_keeps_baseline_reference(self):
  with patch.object(pf,'capture',return_value=self.frame()):
   b=pf.baseline(uuid.UUID(self.rid),pf.BaselineInput(label='baseline',confirmed=True));comp=pf.compare(uuid.UUID(self.rid),pf.CompareInput(baseline_id=b['id']))
  life.archive(uuid.UUID(self.rid),'comparison',uuid.UUID(comp['id']),life.ArchiveInput(archived=True,revision=1))
  self.assertFalse(life.preview_delete(uuid.UUID(self.rid),'baseline',uuid.UUID(b['id']))['can_delete'])
 def test_feedback_requires_linked_implementation_current_checks_and_human(self):
  n=self.note();rid=uuid.UUID(self.rid);nid=uuid.UUID(n['id'])
  life.feedback_status(rid,nid,life.FeedbackState(revision=1,status='adopted',explanation='确认采用此意见'))
  child=str(uuid.uuid4());store.create_run(child,'child',{'run_id':child,'parent_run_id':self.rid,'selected_annotations':[n],'product_spec':self.state['product_spec'],'status':'completed'},'now')
  from app import runner
  runner.initialize_workspace(child)
  life.feedback_status(rid,nid,life.FeedbackState(revision=2,status='implemented',implementation_run_id=child,explanation='子版本完成源码修改'))
  with patch.object(tools,'successful_validation',return_value=None):
   with self.assertRaises(HTTPException):life.feedback_status(rid,nid,life.FeedbackState(revision=3,status='awaiting_confirmation',implementation_run_id=child,explanation='缺少检查不能通过'))
  from app import acceptance
  with patch.object(tools,'successful_validation',return_value={'passed':True,'suite_sha256':acceptance.suite_hash('generated'),'health_sha256':acceptance.health_hash(child)}):
   life.feedback_status(rid,nid,life.FeedbackState(revision=3,status='awaiting_confirmation',implementation_run_id=child,explanation='构建测试记录齐全'))
   with self.assertRaises(HTTPException):life.feedback_status(rid,nid,life.FeedbackState(revision=4,status='closed',implementation_run_id=child,explanation='不能代理人工确认'))
   life.feedback_status(rid,nid,life.FeedbackState(revision=4,status='closed',implementation_run_id=child,explanation='人工核对符合预期',confirmed=True))
  self.assertEqual(c.records(self.rid,'note')[0]['status'],'closed')
  with self.assertRaises(HTTPException):pf.freeze_notes(self.rid,[n['id']])
 def test_rules_scope_conflicts_selection_and_frozen_history(self):
  a=c.add_rule(uuid.UUID(self.rid),c.RuleInput(title='首页颜色',content='使用蓝色',scope_type='page',targets=['/'],constraints={'color':'blue'}))
  b=c.add_rule(uuid.UUID(self.rid),c.RuleInput(title='首页颜色2',content='使用红色',scope_type='page',targets=['/'],constraints={'color':'red'}))
  self.assertEqual(c.freeze_rules(self.rid),[])
  with self.assertRaises(HTTPException):c.freeze_rules(self.rid,scope={'page':['/']})
  frozen=c.freeze_rules(self.rid,[a['id']],{'page':['/']});self.assertEqual(len(frozen),1)
  c.edit_rule(uuid.UUID(self.rid),uuid.UUID(a['id']),c.RuleInput(title='首页颜色',content='修改为绿色',revision=1,scope_type='page',targets=['/'],constraints={'color':'green'}));self.assertEqual(frozen[0]['constraints']['color'],'blue')
  with self.assertRaises(HTTPException):c.freeze_rules(self.rid,[b['id']],{'page':['/other']})
 def test_adoption_is_transactional_and_does_not_close_feedback(self):
  note=self.note()
  with store.db_connection() as conn:
   conn.execute('BEGIN IMMEDIATE');life.adopt_in_transaction(conn,{'run_id':str(uuid.uuid4()),'parent_run_id':self.rid,'selected_annotations':[note]})
  value=c.records(self.rid,'note')[0];self.assertEqual(value['status'],'adopted');self.assertEqual(value['revision'],2)
 def test_visual_save_failure_removes_new_orphans_only(self):
  note=self.note();before=set(pf.folder(self.rid).glob('*.png'))
  with patch.object(pf,'capture',return_value=self.frame()),patch.object(c,'insert',side_effect=HTTPException(409,'capacity')):
   with self.assertRaises(HTTPException):pf.baseline(uuid.UUID(self.rid),pf.BaselineInput(label='failed',confirmed=True))
  self.assertEqual(set(pf.folder(self.rid).glob('*.png')),before)
 def test_invalid_rule_scope_is_rejected(self):
  with self.assertRaises(ValidationError):c.RuleInput(title='title',content='text',scope_type='page')
 def test_visual_mask_region_and_policy_binding(self):
  from PIL import Image
  left=io.BytesIO();right=io.BytesIO();Image.new('RGB',(1000,720),'white').save(left,format='PNG');image=Image.new('RGB',(1000,720),'white');image.paste('black',(0,0,10,10));image.save(right,format='PNG');left.seek(0);right.seek(0)
  ratio,_=pf.difference(left,right,0,{'masks':[{'x':0,'y':0,'width':10,'height':10}]});self.assertEqual(ratio,0)
  left.seek(0);right.seek(0);ratio,_=pf.difference(left,right,0,{'region':{'x':0,'y':0,'width':10,'height':10}});self.assertEqual(ratio,1)
  with self.assertRaises(ValidationError):VisualPolicy(enabled=True,viewport='mobile',masks=[{'x':380,'y':0,'width':30,'height':10}])
  with patch.object(pf,'capture',return_value={**self.frame(),'visual_settings':{'enabled':True,'storage':{'a':'1'}}}):b=pf.baseline(uuid.UUID(self.rid),pf.BaselineInput(label='固定数据',confirmed=True))
  with patch.object(pf,'capture',return_value={**self.frame(),'visual_settings':{'enabled':True,'storage':{'a':'2'}}}):
   with self.assertRaises(HTTPException):pf.compare(uuid.UUID(self.rid),pf.CompareInput(baseline_id=b['id']))
 def test_mobile_png_is_supported(self):
  from PIL import Image
  buf=io.BytesIO();Image.new('RGB',(390,844)).save(buf,format='PNG');pf.save_image(self.rid,str(uuid.uuid4()),base64.b64encode(buf.getvalue()).decode())
 def template_payload(self):
  state={**self.state,'template_id':'tasks','template_digest':templates.load('tasks')['digest']};templates.seed(state)
  return custom.TemplateInput(source_run_id=self.rid,source_digest=project.manifest(self.rid)['digest'],name='通用任务管理',files=project.sources(self.rid),spec=self.state['product_spec'])
 def test_custom_template_preview_revision_seed_and_no_credentials(self):
  payload=self.template_payload();preview=custom.preview(payload);saved=custom.save(custom.SaveTemplate(**payload.model_dump(),token=preview['bundle']['digest'],confirmed=True))
  identifier='custom-'+saved['id'];draft=templates.draft(identifier,templates.DraftInput());self.assertEqual(draft['budget']['calls'],0)
  payload.previous_id=uuid.UUID(saved['id']);v2=custom.prepare(payload);second=custom.save(custom.SaveTemplate(**payload.model_dump(),token=v2['digest'],confirmed=True));self.assertEqual(second['version'],2);self.assertEqual(templates.load(identifier)['version'],1)
  self.assertFalse(custom.clean('const key = "sk-'+('x'*20)+'"'))
  with self.assertRaises(HTTPException):custom.prepare(payload) # old version cannot overwrite newer
 def test_template_rejects_hidden_files_and_stale_sources(self):
  payload=self.template_payload();payload.files['.env']='secret'
  with self.assertRaises(HTTPException):custom.prepare(payload)
  payload.files.pop('.env');self.change_source()
  with self.assertRaises(HTTPException):custom.prepare(payload)
 def test_source_marker_and_ambiguous_text_are_honest(self):
  files={'src/App.tsx':'export const App=()=> <h1 data-pa-source="src/App.tsx:1">标题</h1>','src/Other.tsx':'export const Other=()=> <h1>标题</h1>'}
  r=source_mapping.associate(files,{'source_marker':'src/App.tsx:1','text':'标题'});self.assertFalse(r['uncertain']);self.assertEqual(len(r['locations']),1)
  r=source_mapping.associate(files,{'source_marker':'/Users/private:1','text':'标题'});self.assertTrue(r['uncertain']);self.assertEqual(len(r['locations']),2)
 def test_replay_binding_rejects_stale_and_protects_original(self):
  original=project.sources(self.rid);b=replay.preview_binding(uuid.UUID(self.rid));old=original['src/App.tsx']
  request=replay.ReplayInput(token=b['token'],branches=[replay.Branch(label='保留',decisions=[replay.Decision(action='read_file',path='src/App.tsx'),replay.Decision(action='finish')]),replay.Branch(label='固定修改',decisions=[replay.Decision(action='apply_changes',changes=[project.FileChange(operation='replace',path='src/App.tsx',expected_sha256=project.digest(old),content=old+'\n// branch\n')]),replay.Decision(action='stop')])])
  result=replay.compare(uuid.UUID(self.rid),request);self.assertFalse(result['same_source']);self.assertEqual(result['provider_calls'],0);self.assertEqual(project.sources(self.rid),original)
  self.change_source()
  with self.assertRaises(HTTPException):replay.compare(uuid.UUID(self.rid),request)
 def test_replay_rejects_path_escape_and_stops_at_limit(self):
  files=project.sources(self.rid)
  with self.assertRaises(ValueError):replay.apply_fixed(files,replay.Decision(action='read_file',path='../.env'))
  with self.assertRaises(ValidationError):replay.Decision(action='shell',path='rm')
  b=replay.preview_binding(uuid.UUID(self.rid));branch=replay.Branch(label='限额',decisions=[replay.Decision(action='read_file',path='src/App.tsx')]*3)
  v=replay.compare(uuid.UUID(self.rid),replay.ReplayInput(token=b['token'],step_limit=1,branches=[branch,branch]));self.assertEqual(v['branches'][0]['steps'],1);self.assertEqual(v['branches'][0]['stop_reason'],'达到分支步骤上限')
 def test_replay_redaction_preserves_json_with_quoted_credentials(self):
  data={'input':{'text':'example Bearer test-token"suffix'},'events':['sk-'+('x'*20)]}
  sanitized=replay.redact_tree(data);encoded=json.dumps(sanitized);self.assertNotIn('test-token',encoded);self.assertEqual(json.loads(encoded),sanitized)
 def test_report_comparison_does_not_rank_different_suites(self):
  old={'suite_digest':'a','source_version':'0.5','cases':[{'case':'tasks','strategy':'fixed','mechanism_passed':True,'seconds':2}]};new={**old,'suite_digest':'b','source_version':'0.6'}
  result=evaluation.compare_reports(old,new);self.assertFalse(result['same_suite']);self.assertFalse(result['rows'][0]['comparable'])
del Fixtures
if __name__=='__main__':unittest.main()
