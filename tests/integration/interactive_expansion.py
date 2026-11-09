"""Six extensions through a clean workbench and actual restricted containers; no providers."""
import json,os,sys,time,threading,http.server,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'));sys.path.insert(0,str(ROOT/'scripts'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import main,model,designs,graph,executor,team,store,tools,preview,capability_templates,collaboration,evaluation
import manage,uvicorn
from playwright.sync_api import sync_playwright,expect
calls=[]
def fixed(rid,stage,system,user,schema,**kw):
 calls.append(stage);ctx=json.loads(user)
 if stage.startswith('design-'):return designs.ProductDesign.model_validate(capability_templates.load('tasks')['spec']),{}
 if stage.startswith('agent-planner-'):
  turn=int(stage.rsplit('-',1)[1])
  if turn==1:return schema(action='tool',summary='询问有实际影响的排序选择',tool='user.clarify',arguments={'question':'任务默认按创建时间还是标题排序？','reason':'两种排序都在当前需求范围内','options':['创建时间','标题顺序']}),{}
  assert ctx['collaboration']['clarifications'][0]['answer']=='创建时间'
  return schema.model_validate({'action':'submit','summary':'按回答核对模板','plan':{'steps':[{'title':'读取实现','description':'核对模板','depends_on':[],'acceptance_indices':list(range(len(ctx['acceptance_criteria'])))},{'title':'读取测试','description':'检查模板测试','depends_on':[1]},{'title':'保存版本','description':'记录源码','depends_on':[2]}]}}),{}
 turn=int(stage.rsplit('-',1)[1])
 if stage.startswith('agent-reviewer-'):
  if turn<=2:return schema(action='tool',summary='读取实现与测试',tool='project.read_file',arguments={'path':'src/App.tsx' if turn==1 else 'tests/e2e.spec.ts'}),{}
  assert all(ctx['technical'].get(x) for x in ('build_passed','test_passed','health_passed','environment_verified')),ctx
  return schema(action='submit',summary='固定响应审查',review=team.ReviewReport(verdict='ready_for_manual',summary='合成流程完成，业务另行验收',criteria=[team.CriterionReview(index=i,status='manual',evidence='真实业务效果需另行验收') for i in range(len(ctx['acceptance_criteria']))])),{}
 if turn in (1,3):return model.ExecutorDecision(action='tool',task_id=f'task-{(turn+1)//2}',tool='project.read_file',arguments={'path':'src/App.tsx' if turn==1 else 'tests/e2e.spec.ts'},summary='读取模板'),{}
 if turn==5:return model.ExecutorDecision(action='tool',task_id='task-3',tool='git.snapshot',arguments={},summary='保存快照'),{}
 if turn in (2,4,6):return model.ExecutorDecision(action='complete_task',task_id=f'task-{turn//2}',summary='任务完成'),{}
 return model.ExecutorDecision(action='finish',summary='模板核对完成'),{}
for module in (graph,executor,designs):module.generate_json=fixed
original_settings=model._get_settings
model._get_settings=lambda:{**original_settings(),'enabled':True}
model._client=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No real model'))
server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=8000,log_level='warning',access_log=False,proxy_headers=False));thread=threading.Thread(target=server.run,daemon=True);thread.start()
frontend=http.server.ThreadingHTTPServer(('127.0.0.1',5173),lambda *a,**k:manage.Frontend(*a,directory=str(ROOT/'apps/web/dist'),**k));threading.Thread(target=frontend.serve_forever,daemon=True).start()
report={'provider_calls':0,'checks':[]};errors=[]
def check(name):report['checks'].append(name);print('PASS',name,flush=True)
try:
 for _ in range(100):
  if server.started:break
  time.sleep(.1)
 assert server.started
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:5173');page.wait_for_load_state('networkidle')
   page.screenshot(path=str(ROOT/'outputs/offline-expansion-initial.png'),full_page=True)
   page.locator('summary').filter(has_text='从能力模板开始').click();page.locator('.template-grid article').filter(has=page.get_by_role('heading',name='任务管理',exact=True)).get_by_role('button',name='创建可编辑方案').click()
   expect(page.get_by_label('产品名称',exact=True)).to_have_value('任务管理');assert not calls;check('模板直接创建可编辑方案，不调用模型')
   with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/v1/runs')) as started:page.get_by_role('button',name='确认方案并开始开发 →').click()
   rid=started.value.json()['run_id'];api='http://127.0.0.1:8000/api/v1'
   expect(page.get_by_label('澄清回答')).to_be_visible(timeout=60000);assert len(calls)==1
   blocked=page.request.post(api+'/runs/'+rid+'/resume');assert blocked.status==409
   deadline=time.monotonic()+10
   while main.active_runs and time.monotonic()<deadline:page.wait_for_timeout(50)
   assert not main.active_runs
   main.workflow.checkpointer.conn.close();main.workflow=graph.build_graph()
   page.get_by_role('button',name='创建时间',exact=True).click();page.get_by_role('button',name='保存回答',exact=True).click();expect(page.get_by_label('澄清回答')).to_have_count(0)
   page.get_by_role('button',name='从 SQLite checkpoint 恢复 →').click()
   deadline=time.monotonic()+180
   while time.monotonic()<deadline:
    state=page.request.get(api+'/runs/'+rid).json()
    if state['status'] in main.TERMINAL_STATES and state['status']!='paused':break
    page.wait_for_timeout(300)
   assert state['status']=='completed',state.get('last_error');assert calls.count('agent-planner-0-1')==1;check('澄清暂停、阻止未答恢复、回答后checkpoint继续且不重发提问')
   panel=page.locator('.expansion-panel').filter(has=page.get_by_role('heading',name='协作与项目规则',exact=True))
   panel.locator('summary').filter(has_text='管理长期项目规则').click();panel.get_by_label('规则标题',exact=True).fill('保持配色');panel.get_by_label('规则内容',exact=True).fill('后续修改保持深蓝色');panel.get_by_role('button',name='确认添加规则').click();expect(panel.get_by_text('保持配色 · 生效 · 修订 1')).to_be_visible()
   panel.get_by_role('button',name='编辑',exact=True).click();panel.get_by_label('规则内容',exact=True).fill('后续修改保持深蓝色与浅灰背景');panel.get_by_role('button',name='确认保存修订').click();expect(panel.get_by_text('保持配色 · 生效 · 修订 2')).to_be_visible();check('规则添加、编辑与版本保留')
   page.get_by_role('button',name='打开可交互预览 →').click();expect(page.get_by_alt_text('可点击的隔离产品预览')).to_be_visible(timeout=60000)
   page.get_by_role('button',name='点选修改模式').click();image=page.get_by_alt_text('可点击的隔离产品预览');box=image.bounding_box();image.click(position={'x':box['width']*.25,'y':box['height']*.14})
   page.get_by_label('页面修改意见').fill('将这里的标题改成更清晰的说明');page.get_by_role('button',name='保存点选意见与截图').click();expect(page.get_by_text('将这里的标题改成更清晰的说明',exact=True)).to_be_visible();check('隔离预览点选、截图与源码候选保存')
   page.locator('summary').filter(has_text='本地视觉回归').click();page.get_by_label('基线名称').fill('任务首页空状态');page.get_by_role('button',name='确认当前页面为新基线').click();page.get_by_label('选择比较基线').select_option(label='任务首页空状态 · /');page.get_by_role('button',name='与当前页面比较').click();expect(page.get_by_alt_text('当前页面差异标记')).to_be_visible()
   evid=page.request.get(api+'/collaboration/projects/'+rid+'/evidence').json();assert evid['comparisons'][-1]['changed_ratio']==0,evid['comparisons'][-1];check('明确保存视觉基线，相同页面比较为零')
   # Change only browser state via normal interactions; generated code remains in container.
   from app.preview import act
   act(rid,{'action':'click','x':200,'y':190});act(rid,{'action':'type','text':'新增可见数据'});act(rid,{'action':'key','key':'Tab'});act(rid,{'action':'key','key':'Enter'})
   frame=act(rid,{'action':'snapshot'});from app.page_feedback import compare,CompareInput
   changed=compare(uuid.UUID(rid),CompareInput(baseline_id=evid['baselines'][0]['id']));assert changed['changed_ratio']>0;check('页面内容变化产生可定位视觉差异')
   page.screenshot(path=str(ROOT/'outputs/offline-expansion-desktop.png'),full_page=True)
   page.locator('.interactive-preview').screenshot(path=str(ROOT/'outputs/offline-expansion-preview.png'))
   page.get_by_label('将这里的标题改成更清晰的说明').check();page.get_by_role('button',name='将选中意见带入修改需求').click()
   page.get_by_role('button',name='生成产品方案 →').click();expect(page.get_by_label('产品名称',exact=True)).to_have_value('任务管理')
   latest=designs.list_designs()[0];assert latest['project_rules'][0]['revision']==2 and latest['selected_annotations'];check('页面意见和确认规则冻结到增量方案')
   rule=collaboration.rules(uuid.UUID(rid))['items'][0];collaboration.edit_rule(uuid.UUID(rid),uuid.UUID(rule['id']),collaboration.RuleInput(title=rule['title'],content=rule['content'],source=rule['source'],revision=rule['revision'],active=False));assert designs.get_design(latest['design_id'])['project_rules'][0]['revision']==2;check('撤销规则不改写历史方案')
   page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(ROOT/'outputs/offline-expansion-mobile.png'),full_page=True)
   page.set_viewport_size({'width':1440,'height':1000});page.get_by_role('button',name='工作台设置',exact=True).click()
   with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/workbench/jobs')) as started_job:page.get_by_role('button',name='运行本地离线对照评测').click()
   job=started_job.value.json();assert started_job.value.status==200,job
   deadline=time.monotonic()+300
   while time.monotonic()<deadline:
    job=page.request.get(api+'/workbench/jobs/'+job['id']).json()
    if job['status'] in {'completed','failed','cancelled','interrupted'}:break
    page.wait_for_timeout(500)
   assert job['status']=='completed',job
   result=job['result'];assert result['passed'] and len(result['cases'])==6,result
   expect(page.locator('summary').filter(has_text='机制检查通过')).to_be_visible(timeout=15000);page.locator('summary').filter(has_text='机制检查通过').click()
   page.locator('.expansion-panel').filter(has=page.get_by_role('heading',name='质量与成本评测')).screenshot(path=str(ROOT/'outputs/offline-expansion-evaluation.png'))
   check('界面启动三模板×两策略离线评测，真实容器构建测试与报告查看')
   assert not errors,errors
  finally:browser.close()
 from app import workbench_archive as archive
 import sqlite3
 backup=ROOT/'outputs/extension-fixture-backup.zip';archive.create(ROOT,backup)
 restored=archive.restore(ROOT,backup,str(uuid.uuid4()));destination=Path(restored['location'])
 with sqlite3.connect(destination/'data/runs.sqlite3') as conn:
  kinds={row[0] for row in conn.execute('SELECT DISTINCT kind FROM collaboration_records')}
 assert {'rule','question','note','baseline','comparison','evaluation'}<=kinds,kinds
 assert list((destination/'data/projects'/rid/'visuals').glob('*.png'))
 check('整套备份恢复保留规则、回答、页面意见、视觉图片与评测报告')
 report.update(passed=True,errors=errors,evaluation=result);(ROOT/'outputs/offline-interactive-expansion.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
finally:
 server.should_exit=True;thread.join(15);frontend.shutdown();frontend.server_close()
