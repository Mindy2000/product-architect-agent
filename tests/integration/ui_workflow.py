"""Offline UI-to-container review against a clean public checkout; no provider requests."""
import argparse,io,json,os,sys,threading,time,zipfile
from pathlib import Path
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--report',required=True);args=p.parse_args()
SOURCE=Path(args.source).resolve();REPORT=Path(args.report).resolve();sys.path.insert(0,str(SOURCE/'apps/api'));sys.path.insert(0,str(SOURCE/'scripts'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import main,model,designs,graph,executor,project,store,tools
import manage,uvicorn
from playwright.sync_api import sync_playwright,expect
import http.server
report={'source':str(SOURCE),'provider':'deterministic_fake','paid_calls':0,'checks':[],'scripted_stages':[],'page_errors':[],'console_errors':[]}
def passed(name):report['checks'].append(name);REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASS',name,flush=True)
APP="""import {useState} from 'react'
type Task={id:number;title:string;priority:string;done:boolean}
export default function App(){const [tasks,setTasks]=useState<Task[]>(()=>JSON.parse(localStorage.getItem('review-tasks')||'[]'));const [title,setTitle]=useState(''),[priority,setPriority]=useState('中'),[filter,setFilter]=useState('全部'),[editing,setEditing]=useState<number|null>(null),[draft,setDraft]=useState('');function save(next:Task[]){setTasks(next);localStorage.setItem('review-tasks',JSON.stringify(next))}return <main><h1>流程检查任务管理</h1><label>任务标题<input value={title} onChange={e=>setTitle(e.target.value)}/></label><label>优先级<select aria-label="优先级" value={priority} onChange={e=>setPriority(e.target.value)}>{['高','中','低'].map(v=><option key={v}>{v}</option>)}</select></label><button onClick={()=>{if(title.trim())save([...tasks,{id:Date.now(),title:title.trim(),priority,done:false}]);setTitle('')}}>新增任务</button><nav>{['全部','高','中','低'].map(v=><button key={v} onClick={()=>setFilter(v)}>{v==='全部'?'全部任务':v+'优先级'}</button>)}</nav><ul>{tasks.filter(t=>filter==='全部'||t.priority===filter).map(t=><li key={t.id}><input type="checkbox" aria-label={'完成'+t.title} checked={t.done} onChange={e=>save(tasks.map(x=>x.id===t.id?{...x,done:e.target.checked}:x))}/>{editing===t.id?<><label>编辑任务标题<input value={draft} onChange={e=>setDraft(e.target.value)}/></label><button onClick={()=>{save(tasks.map(x=>x.id===t.id?{...x,title:draft}:x));setEditing(null)}}>保存</button></>:<><span>{t.title}</span><button onClick={()=>{setEditing(t.id);setDraft(t.title)}}>编辑</button></>}<button onClick={()=>save(tasks.filter(x=>x.id!==t.id))}>删除</button></li>)}</ul></main>}
"""
TEST="""import {test,expect} from '@playwright/test';test('中文标题',async({page})=>{await page.goto('/');await expect(page.getByRole('heading',{name:'流程检查任务管理'})).toBeVisible()})"""
SPEC=designs.ProductDesign.model_validate({'product_name':'流程检查任务管理','summary':'离线固定响应验证完整链路，不代表真实模型能力。','target_users':['本地个人用户'],'pages':['任务'],'core_features':['任务增删改与完成','优先级筛选和刷新持久化'],'assumptions':[],'non_goals':['账号系统'],'acceptance_criteria':['可新增任务','支持编辑删除与完成','支持优先级筛选','刷新后保留数据'],'page_details':[{'name':'任务','route':'/','purpose':'管理个人任务','sections':['任务输入','任务列表'],'interactions':['新增编辑删除','优先级筛选']}],'visual_direction':'中文简洁专业','data_notes':['浏览器localStorage']})
first_edit=[False]
def fake(run_id,stage,*a,**kw):
 report['scripted_stages'].append(stage)
 if stage.startswith('design-'):return SPEC,{}
 if stage=='planning':return model.PlanResult.model_validate({'steps':[{'title':'实现任务','description':'实现本地任务管理和测试','depends_on':[],'acceptance_indices':[0,1,2,3]},{'title':'检查文件','description':'检查项目文件','depends_on':[1]},{'title':'保存版本','description':'保存Git版本','depends_on':[2]}]}),{}
 if stage=='diagnosis':return model.RepairDecision(action='repair',reason='修复注入的TypeScript类型错误'),{}
 turn=int(stage.rsplit('-',1)[1]);task=None;tool=None;arguments={};action='tool'
 if turn==1:
  task='task-1';tool='project.apply_changes';arguments={'changes':[{'operation':'replace','path':'src/App.tsx','expected_sha256':project.digest(project.sources(run_id)['src/App.tsx']),'content':APP+"\nconst intentionalFailure: number = 'repair probe'\n"},{'operation':'create','path':'tests/e2e.spec.ts','content':TEST}]}
 elif turn in (2,4,6):action='complete_task';task='task-'+str(turn//2)
 elif turn==3:task='task-2';tool='project.list_files'
 elif turn==5:task='task-3';tool='git.snapshot'
 elif turn in (7,11):action='finish'
 elif turn==8:task='repair-1';tool='project.read_file';arguments={'path':'src/App.tsx'}
 elif turn==9:task='repair-1';tool='project.apply_changes';arguments={'changes':[{'operation':'replace','path':'src/App.tsx','expected_sha256':project.digest(project.sources(run_id)['src/App.tsx']),'content':APP}]}
 elif turn==10:action='complete_task';task='repair-1'
 else:raise AssertionError('Unexpected offline decision '+stage)
 return model.ExecutorDecision(action=action,task_id=task,tool=tool,arguments=arguments,summary='离线流程核对 '+str(turn)),{}
for module in (model,designs,graph,executor):module.generate_json=fake
model.OpenAI=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Remote model forbidden'))
original_settings=model._get_settings
model._get_settings=lambda:{**original_settings(),'enabled':True}
execute=tools.execute
def intercepted(context,name,arguments):
 value=execute(context,name,arguments)
 if name=='project.apply_changes' and value['ok'] and not first_edit[0]:first_edit[0]=True;store.set_pause_requested(context.run_id,True)
 return value
tools.execute=intercepted
server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=8000,log_level='warning',access_log=False,proxy_headers=False))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
handler=lambda *a,**k:manage.Frontend(*a,directory=str(SOURCE/'apps/web/dist'),**k)
frontend=http.server.ThreadingHTTPServer(('127.0.0.1',5173),handler);threading.Thread(target=frontend.serve_forever,daemon=True).start()
try:
 for _ in range(100):
  if server.started:break
  time.sleep(.1)
 assert server.started
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
   page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
   page.on('console',lambda m:report['console_errors'].append(m.text) if m.type=='error' else None)
   page.goto('http://127.0.0.1:5173/');page.wait_for_load_state('networkidle')
   original_runs=len(page.request.get('http://127.0.0.1:8000/api/v1/runs').json())
   passed('干净源码安装后的中文工作台载入')
   page.get_by_placeholder('例如：做一个简洁的个人任务管理工具，可以添加任务、标记完成、按优先级筛选，并在刷新后保留数据……').fill('做一个中文任务管理工具，支持新增编辑删除、完成切换、优先级筛选和刷新持久化。')
   page.locator('#acceptance-choice').select_option('todo-v1')
   page.get_by_role('button',name='生成产品方案 →').click()
   expect(page.get_by_label('产品名称',exact=True)).to_have_value(SPEC.product_name,timeout=15000)
   page.get_by_label('产品名称',exact=True).fill('流程检查任务管理·已确认')
   page.get_by_role('button',name='保存草稿',exact=False).click()
   expect(page.get_by_text('修改已保存，尚未启动开发。',exact=True)).to_be_visible()
   assert len(page.request.get('http://127.0.0.1:8000/api/v1/runs').json())==original_runs
   passed('需求生成方案、编辑保存，确认前不开发')
   with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/v1/runs')) as created:
    page.get_by_role('button',name='确认方案并开始开发 →').click()
   rid=created.value.json()['run_id'];report['run_id']=rid
   deadline=time.monotonic()+30
   while time.monotonic()<deadline:
    state=page.request.get('http://127.0.0.1:8000/api/v1/runs/'+rid).json()
    if state['status']=='paused':break
    page.wait_for_timeout(200)
   assert state['status']=='paused',state.get('last_error')
   expect(page.get_by_role('button',name='从 SQLite checkpoint 恢复 →')).to_be_visible(timeout=20000)
   passed('首次编辑后暂停，源码及checkpoint保留')
   with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/resume')) as resumed:
    page.get_by_role('button',name='从 SQLite checkpoint 恢复 →').click()
   assert resumed.value.status==200
   deadline=time.monotonic()+240
   while time.monotonic()<deadline:
    state=page.request.get('http://127.0.0.1:8000/api/v1/runs/'+rid).json()
    if state['status'] in main.TERMINAL_STATES:break
    page.wait_for_timeout(500)
   assert state['status']=='completed',state.get('last_error')
   assert state['repair_round']==1 and state['build_status']==state['test_status']=='passed'
   assert any(e['stage']=='build' and e['status']=='failed' for e in state['trace'])
   passed('真实容器构建失败→诊断→一次修复→标准Playwright验收通过')
   assert (SOURCE/'generated-projects'/rid/'tests/acceptance.spec.ts').read_bytes()==(SOURCE/'benchmarks/todo-v1.spec.ts').read_bytes()
   passed('发布包内独立验收文件实际被加载执行')
   with page.expect_download() as event:
    page.get_by_role('link',name='源码 ZIP',exact=True).first.click()
   download=event.value;download.save_as(str(REPORT.parent/'flow-source-export.zip'))
   with zipfile.ZipFile(download.path()) as z:assert not any('.env' in n or 'app.sqlite3' in n for n in z.namelist())
   for kind in ['trace','git_diff','manifest','report']:
    assert page.request.get(f'http://127.0.0.1:8000/api/v1/runs/{rid}/artifact/{kind}').status==200
   passed('界面下载源码ZIP、Trace、Git diff、清单和交付报告')
   page.get_by_role('button',name='打开可交互预览 →').click()
   expect(page.get_by_alt_text('可点击的隔离产品预览')).to_be_visible(timeout=30000)
   page.get_by_role('button',name='关闭预览',exact=True).click()
   passed('工作台打开并关闭容器交互预览')
   versions=page.request.get(f'http://127.0.0.1:8000/api/v1/runs/{rid}/versions').json()
   restored=page.request.post(f'http://127.0.0.1:8000/api/v1/runs/{rid}/restore',data={'commit':versions[0]['commit']}).json()
   assert restored['status']=='paused' and restored['parent_run_id']==rid
   assert project.sources(restored['run_id'])==project.sources(rid)
   passed('源码恢复为独立暂停副本，原版本保持完成')
   page.screenshot(path=str(REPORT.parent/'flow-workbench.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
   assert not report['page_errors'],report['page_errors']
   assert not report['console_errors'],report['console_errors']
   passed('桌面及手机宽度无溢出、无页面或控制台错误')
   report.update(passed=True,repair_rounds=1,build_status=state['build_status'],test_status=state['test_status'],browser_output=state.get('test_output',''))
  finally:browser.close()
finally:
 server.should_exit=True;thread.join(timeout=20);frontend.shutdown();frontend.server_close();REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
