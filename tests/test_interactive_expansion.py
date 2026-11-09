import base64,io,json,sys,tempfile,unittest,uuid,shutil,threading
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import store,runner,project,collaboration as c,page_feedback as pf,capability_templates as templates,tools,team,designs,model
class ExpansionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.stack=ExitStack();original=runner.ROOT
  shutil.copytree(original/'templates',self.root/'templates');(self.root/'runtime').mkdir();shutil.copyfile(original/'runtime/preview-worker.cjs',self.root/'runtime/preview-worker.cjs')
  for obj,name,value in [(runner,'ROOT',self.root),(runner,'GENERATED_ROOT',self.root/'generated-projects'),(runner,'OUTPUT_ROOT',self.root/'outputs'),(store,'DATA_DIR',self.root/'data'),(store,'RUNS_DB',self.root/'data/runs.sqlite3'),(model,'DATA_DIR',self.root/'data'),(model,'USAGE_DB',self.root/'data/usage.sqlite3')]:self.stack.enter_context(patch.object(obj,name,value))
  self.rid=str(uuid.uuid4());self.state={'run_id':self.rid,'collaboration_version':1,'status':'paused','product_spec':templates.load('tasks')['spec'],'trace':[]};store.create_run(self.rid,'fixture',self.state,'now');runner.initialize_workspace(self.rid)
 def tearDown(self):self.stack.close();self.temp.cleanup()
 def change_source(self):
  old=project.sources(self.rid)['src/App.tsx'];project.apply_changes(self.rid,[project.FileChange(operation='replace',path='src/App.tsx',content=old+'\n// new revision\n',expected_sha256=project.digest(old))],'test_source_change')
 def test_rule_revisions_freeze_revoke_and_lineage(self):
  r=c.add_rule(uuid.UUID(self.rid),c.RuleInput(title='配色',content='保持深蓝色'))
  snapshot=c.freeze_rules(self.rid);c.edit_rule(uuid.UUID(self.rid),uuid.UUID(r['id']),c.RuleInput(title='配色',content='保持浅蓝色',revision=1,active=False))
  self.assertEqual(snapshot[0]['content'],'保持深蓝色');self.assertEqual(c.freeze_rules(self.rid),[])
  with self.assertRaises(HTTPException):c.edit_rule(uuid.UUID(self.rid),uuid.UUID(r['id']),c.RuleInput(title='配色',content='过期编辑',revision=1))
  child=str(uuid.uuid4());store.create_run(child,'child',{'parent_run_id':self.rid},'now');self.assertEqual(c.lineage(child),self.rid)
  self.assertEqual(len(c.records(self.rid,'rule')[0]['history']),2)
 def test_questions_idempotent_bounded_answer_and_roles(self):
  args=c.QuestionInput(question='列表默认按什么顺序排列？',reason='两种顺序均符合范围',options=['创建时间','标题顺序'])
  ctx=tools.ToolContext(self.rid,'task-1','ask-1',agent_id='developer');q=c.ask(ctx,args);self.assertEqual(c.ask(ctx,args),q)
  self.assertTrue(team.allowed('developer','user.clarify',self.state));self.assertFalse(team.allowed('developer','user.clarify',{}))
  c.answer(uuid.UUID(self.rid),uuid.UUID(q['id']),c.Answer(answer='创建时间'));self.assertIsNone(c.pending(self.rid))
  with self.assertRaises(HTTPException):c.answer(uuid.UUID(self.rid),uuid.UUID(q['id']),c.Answer(answer='标题顺序'))
  for n in range(2,4):
   v=c.ask(tools.ToolContext(self.rid,'task-1','ask-'+str(n),agent_id='developer'),args);c.answer(uuid.UUID(self.rid),uuid.UUID(v['id']),c.Answer(answer='创建时间'))
  with self.assertRaises(project.ProjectError):c.ask(tools.ToolContext(self.rid,'task-1','ask-4',agent_id='developer'),args)
 def test_stale_question_cannot_resume_on_changed_source(self):
  q=c.ask(tools.ToolContext(self.rid,'task-1','ask',agent_id='planner'),c.QuestionInput(question='列表是否按创建时间排序？',reason='确认默认排序方向',options=['正序','倒序']))
  self.change_source()
  with self.assertRaises(HTTPException):c.answer(uuid.UUID(self.rid),uuid.UUID(q['id']),c.Answer(answer='正序'))
 def test_templates_are_ready_without_model_and_seed_once(self):
  with patch.object(model,'_client',side_effect=AssertionError('no model')):
   for name in templates.IDS:
    value=templates.draft(name,templates.DraftInput());self.assertEqual(value['status'],'ready');self.assertEqual(value['budget']['calls'],0)
    spec=templates.load(name);self.assertIn('tests/e2e.spec.ts',spec['files'])
   state={**self.state,'template_id':'tasks','template_digest':templates.load('tasks')['digest']};templates.seed(state);before=project.manifest(self.rid)['digest'];templates.seed(state);self.assertEqual(before,project.manifest(self.rid)['digest'])
   with self.assertRaises(runner.SandboxError):templates.seed({**state,'parent_run_id':self.rid})
   with self.assertRaises(runner.SandboxError):templates.seed({**state,'template_digest':'bad'})
 def frame(self,color='white'):
  from PIL import Image
  image=Image.new('RGB',(1000,720),color);buf=io.BytesIO();image.save(buf,format='PNG')
  return {'image':base64.b64encode(buf.getvalue()).decode(),'width':1000,'height':720,'route':'/','source_digest':project.manifest(self.rid)['digest'],'environment':{'image_id':'fixed','digest':'env'},'preview_policy':'policy','selection':{'tag':'h1','text':'标题','selector':'h1','rect':{'x':1,'y':2,'width':10,'height':10}}}
 def test_visual_baseline_requires_confirmation_and_detects_change(self):
  with patch.object(pf,'capture',return_value=self.frame()):
   with self.assertRaises(HTTPException):pf.baseline(uuid.UUID(self.rid),pf.BaselineInput(label='首页'))
   b=pf.baseline(uuid.UUID(self.rid),pf.BaselineInput(label='首页',confirmed=True));r=pf.compare(uuid.UUID(self.rid),pf.CompareInput(baseline_id=b['id']));self.assertEqual(r['changed_ratio'],0)
  with patch.object(pf,'capture',return_value=self.frame('black')):
   r=pf.compare(uuid.UUID(self.rid),pf.CompareInput(baseline_id=b['id']));self.assertEqual(r['changed_ratio'],1)
   n=pf.add_note(uuid.UUID(self.rid),pf.NoteInput(comment='这里改成浅色',comparison_id=r['id']));self.assertEqual(n['comparison_id'],r['id'])
  self.assertTrue(pf.image(uuid.UUID(self.rid),r['diff_image']).path)
  with self.assertRaises(HTTPException):pf.image(uuid.UUID(self.rid),'../../secret')
 def test_visual_environment_or_route_mismatch_rejected(self):
  with patch.object(pf,'capture',return_value=self.frame()):b=pf.baseline(uuid.UUID(self.rid),pf.BaselineInput(label='首页',confirmed=True))
  for field,value in [('route','/other'),('environment',{'digest':'changed'}),('preview_policy','changed')]:
   with patch.object(pf,'capture',return_value={**self.frame(),field:value}):
    with self.assertRaises(HTTPException):pf.compare(uuid.UUID(self.rid),pf.CompareInput(baseline_id=b['id']))
 def test_notes_are_scoped_and_frozen_not_arbitrary_source(self):
  with patch.object(pf,'capture',return_value=self.frame()):n=pf.add_note(uuid.UUID(self.rid),pf.NoteInput(comment='调整这个标题'))
  selected=pf.freeze_notes(self.rid,[n['id']]);self.assertEqual(selected[0]['comment'],'调整这个标题');self.assertNotIn('environment',selected[0])
  with self.assertRaises(HTTPException):pf.freeze_notes(None,[n['id']])
  self.change_source()
  with self.assertRaises(HTTPException):pf.freeze_notes(self.rid,[n['id']])
 def test_external_source_change_cannot_be_used_for_feedback(self):
  with patch.object(pf,'capture',return_value=self.frame()):n=pf.add_note(uuid.UUID(self.rid),pf.NoteInput(comment='调整当前页面'))
  (runner.workspace_for(self.rid)/'src/App.tsx').write_text('external tampering')
  with self.assertRaises(project.ProjectError):pf.freeze_notes(self.rid,[n['id']])
 def test_rule_and_visual_records_are_in_workbench_backup_scope(self):
  from app.workbench_archive import allowed
  self.assertTrue(allowed('data/runs.sqlite3'));self.assertTrue(allowed('data/projects/'+self.rid+'/visuals/'+str(uuid.uuid4())+'.png'))
if __name__=='__main__':unittest.main()
