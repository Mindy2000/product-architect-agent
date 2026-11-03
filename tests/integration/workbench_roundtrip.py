"""Three-feature browser acceptance using synthetic data in an isolated source checkout."""
import argparse,hashlib,json,os,sqlite3,subprocess,sys,threading,time,uuid,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--report',required=True);args=p.parse_args();SOURCE=Path(args.source);REPORT=Path(args.report)
sys.path.insert(0,str(SOURCE/'apps/api'));sys.path.insert(0,str(SOURCE/'scripts'))
TEST_KEY='gui-fixture-'+uuid.uuid4().hex
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false';os.environ.pop('MODEL_API_KEY',None)
from app import main,runner,store,model,library,references,graph,project
from app.designs import ProductDesign
from contextlib import closing
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
import uvicorn,http.server,manage
report={'checks':[],'paid_calls':0,'dify_requests':0,'source':str(SOURCE),'page_errors':[],'console_errors':[]}
def passed(name):report['checks'].append(name);REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASS',name,flush=True)
rid=str(uuid.uuid4());spec=ProductDesign.model_validate({'product_name':'工作台灾备合成项目','summary':'仅测试工作台恢复，不调用模型。','target_users':['测试人员'],'pages':['首页'],'core_features':['中文标题','本地恢复'],'assumptions':[],'non_goals':['外部服务'],'acceptance_criteria':['标题可见','构建通过','测试通过','版本保留'],'page_details':[{'name':'首页','route':'/','purpose':'灾备验证','sections':['标题'],'interactions':['浏览']}],'visual_direction':'简洁中文','data_notes':['无外部请求']}).model_dump()
state={'run_id':rid,'user_request':'PRIVATE SYNTHETIC REQUEST FOR BACKUP','runtime_version':2,'requirements_confirmed':True,'product_spec':spec,'acceptance_criteria':spec['acceptance_criteria'],'status':'paused','can_resume':True,'stage':'generation','repair_round':0,'repair_limit':0,'trace':[],'created_at':'2026-11-03'}
store.create_run(rid,state['user_request'],state,state['created_at']);runner.initialize_workspace(rid)
runner.apply_generated_files(rid,[{'path':'src/App.tsx','content':"export default function App(){return <main><h1>工作台灾备合成项目</h1></main>}"},{'path':'tests/e2e.spec.ts','content':"import {test,expect} from '@playwright/test';test('恢复后中文界面',async({page})=>{await page.goto('/');await expect(page.getByRole('heading',{name:'工作台灾备合成项目'})).toBeVisible()})"}])
cp=graph.build_graph();cp.update_state({'configurable':{'thread_id':rid}},state,as_node='generation');cp.checkpointer.conn.close()
with closing(model._connect()) as c:c.execute('INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),rid,'synthetic-fixture','fixture','offline',1.75,1.75,1,1,'settled','2026-11-03'));c.commit()
# Real document indexing and direct references, all synthetic and local.
api=FastAPI();api.include_router(library.router);api.include_router(references.router)
with TestClient(api) as c:
 cid=c.post('/api/v1/library/collections',json={'name':'灾备合成知识库','description':'独立测试'}).json()['id']
 response=c.post('/api/v1/library/collections/'+cid+'/documents?filename=灾备文档.md',content='# 灾备规则\n恢复后应保留知识库和原始资料。'.encode());assert response.status_code<300,response.text
 ref=c.post('/api/v1/references',json={'title':'灾备参考','content':'PRIVATE SYNTHETIC REFERENCE'});assert ref.status_code==200
appdata=SOURCE/'data/applications'/rid;appdata.mkdir(parents=True)
with closing(sqlite3.connect(appdata/'app.sqlite3')) as c:c.execute('CREATE TABLE fixture(value TEXT)');c.execute("INSERT INTO fixture VALUES ('SYNTHETIC BUSINESS DATA')");c.commit()
(appdata/'attachment.txt').write_text('SYNTHETIC ATTACHMENT')
(SOURCE/'data/dify').mkdir(exist_ok=True);(SOURCE/'data/dify/free-policy.json').write_text('{"test_only":true,"used":35}')
server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=8000,access_log=False,log_level='warning',proxy_headers=False));thread=threading.Thread(target=server.run,daemon=True);thread.start()
frontend=http.server.ThreadingHTTPServer(('127.0.0.1',5173),lambda *a,**k:manage.Frontend(*a,directory=str(SOURCE/'apps/web/dist'),**k));threading.Thread(target=frontend.serve_forever,daemon=True).start()
restored=None
try:
 for _ in range(100):
  if server.started:break
  time.sleep(.1)
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
   page.on('pageerror',lambda e:report['page_errors'].append(str(e)));page.on('console',lambda m:report['console_errors'].append(m.text) if m.type=='error' else None)
   page.goto('http://127.0.0.1:5173/');page.wait_for_load_state('networkidle');expect(page.get_by_role('heading',name='首次使用向导')).to_be_visible();passed('首次打开未配置工作台自动进入可视化向导')
   page.get_by_label('向导API密钥',exact=True).fill(TEST_KEY);page.get_by_label('向导累计费用上限').fill('20');page.get_by_role('checkbox',name='我允许文字模型产生费用',exact=False).check();page.get_by_role('button',name='保存本地配置',exact=True).click()
   expect(page.get_by_text('已保存到本机配置。请先停止工作台，再重新启动；本次没有调用任何供应商。',exact=True)).to_be_visible();expect(page.get_by_label('向导API密钥',exact=True)).to_have_value('');assert os.environ['MODEL_CALLS_ENABLED']=='false';passed('界面保存合成密钥与预算，隐藏输入清空、提示重启且未调用模型')
   page.get_by_role('button',name='预览工作台备份范围').click();expect(page.get_by_role('button',name='下载整套工作台备份')).to_be_visible()
   page.get_by_role('button',name='下载整套工作台备份').click()
   expect(page.get_by_role('link',name='下载已完成的工作台备份')).to_be_visible(timeout=30000)
   with page.expect_download() as event:page.get_by_role('link',name='下载已完成的工作台备份').click()
   backup=REPORT.parent/'workbench-synthetic-backup.zip';event.value.save_as(str(backup))
   with zipfile.ZipFile(backup) as z:
    names=z.namelist();assert not any(n=='.env' or 'node_modules' in n for n in names);assert 'data/checkpoints.sqlite3' in names
    assert all(TEST_KEY.encode() not in z.read(n) for n in names)
   passed('浏览器下载整套工作台备份，检查点在内、密钥和依赖排除')
   page.get_by_label('选择工作台备份ZIP',exact=True).set_input_files(str(backup));expect(page.get_by_text('校验通过。恢复将创建独立工作台副本；原目录保留，新副本密钥为空、模型调用关闭。',exact=True)).to_be_visible()
   expect(page.get_by_role('button',name='确认恢复整套工作台')).to_be_disabled();page.get_by_role('checkbox',name='确认创建独立副本',exact=False).check()
   with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/workbench/jobs')) as response:page.get_by_role('button',name='确认恢复整套工作台').click()
   result=response.value.json();assert response.value.status==200,result
   deadline=time.monotonic()+40
   while time.monotonic()<deadline:
    task=page.request.get('http://127.0.0.1:8000/api/v1/workbench/jobs/'+result['id']).json()
    if task['status'] not in {'queued','running'}:break
    page.wait_for_timeout(200)
   assert task['status']=='completed',task.get('error');restored=Path(task['result']['location']);report['restored']=str(restored);passed('上传预览、明确确认、恢复到独立工作台目录')
   assert (SOURCE/'data/applications'/rid/'attachment.txt').read_text()==(restored/'data/applications'/rid/'attachment.txt').read_text()
   assert 'MODEL_CALLS_ENABLED=false' in (restored/'.env').read_text();assert 'MODEL_API_KEY=\n' in (restored/'.env').read_text();assert (restored/'data/dify/free-policy.json').read_text()=='{"test_only":true,"used":35}'
   passed('原数据保留，附件与Dify本地额度记录复制，新副本密钥为空且调用关闭')
   page.get_by_role('button',name='预览脱敏诊断内容').click();expect(page.get_by_label('诊断导出预览',exact=True)).to_be_visible();text=page.get_by_label('诊断导出预览',exact=True).inner_text()
   for marker in [TEST_KEY,'PRIVATE SYNTHETIC',str(SOURCE),'SYNTHETIC BUSINESS DATA']:assert marker not in text
   with page.expect_download() as event:page.get_by_role('link',name='下载已预览的诊断 ZIP').click()
   diagnostic=REPORT.parent/'workbench-diagnostic.zip';event.value.save_as(str(diagnostic))
   with zipfile.ZipFile(diagnostic) as z:assert json.loads(z.read('diagnostic.json'))==json.loads(text)
   passed('诊断预览与下载一致，不含密钥、路径、需求、文档或业务正文')
   page.screenshot(path=str(REPORT.parent/'workbench-center-desktop.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');page.screenshot(path=str(REPORT.parent/'workbench-center-mobile.png'),full_page=True)
   assert not report['page_errors'],report['page_errors'];assert not report['console_errors'],report['console_errors'];passed('中文设置页面桌面与手机宽度正常，无页面/控制台错误')
  finally:browser.close()
finally:
 server.should_exit=True;thread.join(timeout=20);frontend.shutdown();frontend.server_close();REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
assert restored
# Fresh process loads the restored workbench, never reuses the source application's module globals.
program="""
import json,sqlite3,sys
from contextlib import closing
from app import runner,store,graph,project,library,model
rid,cid=sys.argv[1:3]
assert model._get_settings()['enabled'] is False
assert store.get_run(rid)['status']=='paused'
assert runner.version_snapshots(rid)
with closing(model._connect()) as c:assert abs(c.execute('SELECT SUM(actual_cny) FROM model_usage').fetchone()[0]-1.75)<.0001
with closing(sqlite3.connect(runner.ROOT/'data/applications'/rid/'app.sqlite3')) as c:assert c.execute('SELECT value FROM fixture').fetchone()[0]=='SYNTHETIC BUSINESS DATA'
assert library.capture([cid],'灾备规则')['chunks']
workflow=graph.build_graph()
try:
 config={'configurable':{'thread_id':rid},'recursion_limit':256}
 assert workflow.get_state(config).next
 result=workflow.invoke(None,config)
 assert result['status']=='completed',result.get('last_error')
finally:workflow.checkpointer.conn.close()
print('RESTORED CHECKPOINT, GIT, COST, KNOWLEDGE, BUSINESS DATA AND CONTAINER BUILD/TEST PASSED',flush=True)
"""
env=dict(os.environ);env['PYTHONPATH']=str(restored/'apps/api');subprocess.run([sys.executable,'-c',program,rid,cid],cwd=restored,env=env,check=True)
passed('独立进程读取恢复数据、Git版本、费用、本地检索、业务库，并恢复checkpoint完成真实容器构建测试')
report['passed']=True;REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
