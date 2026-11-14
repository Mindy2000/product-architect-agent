"""Browser forms over actual components and synthetic TestClient services; no live API."""
import json,os,sys
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'apps/api'));sys.path.insert(0,str(ROOT/'tests'))
from app import collaboration,library,workbench,evaluation,model
from app.visual_policy import VisualPolicy
from test_interactive_expansion import ExpansionTests
from test_improvement_batch2 import QualityTests
report={'checks':[],'provider_requests':0,'live_api_requests':0,'errors':[]}
def passed(name):
 report['checks'].append(name);(ROOT/'outputs/improvement-batch2-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASS',name,flush=True)
fixture=ExpansionTests();fixture.setUp();quality=QualityTests();quality.setUp()
try:
 (fixture.root/'.env').write_text('MODEL_API_KEY=synthetic-test-key\nMODEL_NAME=deepseek-flash\nMODEL_CALLS_ENABLED=false\nMODEL_BUDGET_CNY=99\n')
 app=FastAPI()
 for router in (collaboration.router,library.router,workbench.router,evaluation.router):app.include_router(router)
 @app.post('/api/v1/test/visual')
 def visual(value:VisualPolicy):return value.model_dump()
 @app.get('/api/v1/runs/{rid}/knowledge-evidence')
 def evidence(rid:str):return library.search(library.SearchInput(collection_ids=[quality.cid],query='姓名必填'),record=False)
 with patch.object(model,'_client',side_effect=AssertionError('provider forbidden')),patch.object(workbench,'local_command',return_value=False),TestClient(app) as client,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,channel=os.environ.get('PA_UI_BROWSER_CHANNEL') or None)
  try:
   page=browser.new_page(viewport={'width':1360,'height':1000});unexpected=[]
   page.on('pageerror',lambda error:report['errors'].append(str(error)))
   allowed={'GET','POST','PUT'}
   def route(r):
    request=r.request;url=urlparse(request.url)
    if url.netloc=='127.0.0.1:8792':return r.continue_()
    if url.netloc!='127.0.0.1:8000':unexpected.append(request.url);return r.abort()
    forbidden=('/jobs','/backups/restore','/backups/export','/diagnostics')
    if request.method not in allowed or (request.method!='GET' and any(x in url.path for x in forbidden)):
     unexpected.append(request.method+' '+url.path);return r.abort()
    response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,headers={'Content-Type':'application/json'} if request.post_data_buffer else {})
    if response.status_code>=400:unexpected.append(str(response.status_code)+' '+url.path+' '+response.text[:100])
    r.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
   page.route('**/*',route)
   page.goto('http://127.0.0.1:8792/?run_id='+fixture.rid);page.wait_for_load_state('networkidle')
   print('DOM controls:',page.get_by_role('button').all_text_contents(),flush=True)
   page.get_by_text('视觉对照设置',exact=True).click()
   page.get_by_label('开启独立视觉模式').check()
   expect(page.get_by_role('button',name='添加区域后填写坐标')).to_be_disabled()
   page.get_by_role('button',name='添加合成浏览器数据').click()
   page.get_by_label('合成浏览器数据名称1').fill('fixture')
   page.get_by_label('合成浏览器数据值1').fill('kept-string')
   page.get_by_role('button',name='应用设置并刷新截图').click()
   expect(page.get_by_role('button',name='添加区域后填写坐标')).to_be_enabled()
   picker=page.get_by_label('视觉区域选择截图');picker.scroll_into_view_if_needed();box=picker.bounding_box()
   page.mouse.move(box['x']+box['width']*.1,box['y']+box['height']*.1);page.mouse.down();page.mouse.move(box['x']+box['width']*.3,box['y']+box['height']*.3);page.mouse.up()
   expect(page.get_by_label('遮罩1 width')).to_have_value('200')
   page.get_by_role('button',name='应用设置并刷新截图').click()
   expect(page.get_by_label('已应用设置')).to_contain_text('kept-string')
   expect(page.get_by_label('已应用设置')).to_contain_text('"masks":[{')
   assert len(json.loads(page.get_by_label('已应用设置').inner_text())['masks'])==1
   passed('独立模式预先应用、键值表单和拖选遮罩发送正确坐标')
   page.get_by_label('视口',exact=True).select_option('mobile')
   expect(page.get_by_label('遮罩1 width')).to_have_count(0)
   expect(page.get_by_role('button',name='添加区域后填写坐标')).to_be_disabled()
   page.get_by_role('button',name='应用设置并刷新截图').click()
   page.get_by_label('点选用途').select_option('region');page.get_by_role('button',name='添加区域后填写坐标').click()
   page.get_by_label('比较区域 width').fill('150');page.get_by_role('button',name='应用设置并刷新截图').click()
   expect(page.get_by_label('已应用设置')).to_contain_text('"width":150')
   assert json.loads(page.get_by_label('已应用设置').inner_text())['region']['width']==150
   page.set_viewport_size({'width':390,'height':844})
   print('Overflow candidates:',page.evaluate("Array.from(document.querySelectorAll('*')).filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>[e.tagName,e.className,e.getAttribute('aria-label')]).slice(0,15)"),flush=True)
   assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
   page.screenshot(path=str(ROOT/'outputs/improvement-batch2-mobile.png'),full_page=True)
   passed('切换视口清除旧坐标，键盘可编辑比较区域，手机无横向溢出')
   page.set_viewport_size({'width':1360,'height':1000})
   page.get_by_role('button',name='测试：规则').click();page.wait_for_load_state('networkidle')
   page.get_by_text('管理长期项目规则 · 0',exact=True).click()
   print('Rule labels:',page.locator('label').all_text_contents(),flush=True)
   page.get_by_label('规则标题').fill('主色规则甲');page.get_by_label('规则内容').fill('使用蓝色')
   page.get_by_role('button',name='添加规则字段').click();page.get_by_label('规则字段名称1').fill('color');page.get_by_label('规则字段值1').fill('blue')
   page.get_by_role('button',name='确认添加规则').click()
   expect(page.get_by_text('管理长期项目规则 · 1',exact=True)).to_be_visible()
   page.get_by_label('规则标题').fill('主色规则乙');page.get_by_label('规则内容').fill('使用红色');page.get_by_label('规则字段值1').fill('red')
   page.get_by_role('button',name='确认添加规则').click();expect(page.get_by_text('管理长期项目规则 · 2',exact=True)).to_be_visible()
   page.get_by_text('本次修改适用规则',exact=True).click()
   expect(page.get_by_text('重叠范围的结构化要求不同',exact=False)).to_be_visible()
   page.get_by_label('主色规则乙：使用红色').uncheck()
   expect(page.get_by_text('重叠范围的结构化要求不同',exact=False)).to_have_count(0)
   passed('规则逐行创建后实时更新选择器，展示冲突，取消选择后解除')
   page.get_by_role('button',name='测试：资料').click();page.wait_for_load_state('networkidle')
   page.get_by_text('项目知识库 · 已选 0 / 6',exact=True).click()
   page.get_by_label('管理哪个知识库').select_option(quality.cid)
   print('Document buttons:',page.get_by_role('button').all_text_contents(),flush=True)
   page.get_by_role('button',name='查看分段').click()
   page.get_by_text('文档质量与版本检查',exact=True).click()
   expect(page.get_by_text('文档指纹：doc-sha',exact=True)).to_be_visible()
   page.get_by_label('检索资料',exact=True).fill('姓名必填');page.get_by_role('button',name='检索并查看来源').click()
   expect(page.get_by_text('相关片段 · 1 条',exact=False)).to_be_visible()
   page.get_by_label('检索资料',exact=True).fill('zzzzunknown');page.get_by_role('button',name='检索并查看来源').click()
   expect(page.get_by_text('本地检索已完成，但没有满足当前条件的片段',exact=False)).to_be_visible()
   passed('文档质量指纹与本地检索命中/无结果区分可见')
   page.get_by_role('button',name='测试：引用').click();page.get_by_text('查看本次保存的知识库引用原文',exact=True).click()
   page.get_by_role('button',name='核对来源当前状态').click()
   expect(page.get_by_text('当前文档与片段仍有效（不代表语义相关性通过）',exact=True)).to_be_visible()
   passed('引用原文与当前片段核对通过且不声称语义验收')
   page.get_by_role('button',name='测试：设置').click();page.wait_for_load_state('networkidle')
   expect(page.get_by_text('仅检测是否填写；未联网验证',exact=False)).to_be_visible()
   page.get_by_label('向导默认文字模型').select_option('deepseek-v4-pro')
   page.get_by_role('button',name='保存本地配置',exact=True).click()
   expect(page.get_by_text('已保存到本机配置。请先停止工作台，再重新启动；本次没有调用任何供应商。',exact=True)).to_be_visible()
   assert 'MODEL_NAME=deepseek-v4-pro' in (fixture.root/'.env').read_text()
   assert 'MODEL_CALLS_ENABLED=false' in (fixture.root/'.env').read_text()
   passed('默认模型表单只保存合成配置，许可未自动打开')
   page.get_by_role('button',name='测试：分支').click();page.get_by_label('分支1步骤1').select_option('apply_changes')
   page.get_by_label('完整替换内容').fill('export default function App(){return null}')
   value=json.loads(page.get_by_label('分支结果').inner_text())
   assert value[0]['decisions'][0]['changes'][0]['expected_sha256']=='a'*64
   assert value[1]['decisions'][0]['action']=='stop'
   passed('固定分支表单绑定文件指纹，保留其他分支且未启动重放')
   page.screenshot(path=str(ROOT/'outputs/improvement-batch2-desktop.png'),full_page=True)
   assert not unexpected,unexpected
   assert not report['errors'],report['errors']
   passed('无页面异常、无原工作台或外部供应商请求')
  finally:browser.close()
finally:
 quality.doCleanups();fixture.tearDown()
