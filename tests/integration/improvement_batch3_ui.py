"""Real React components against synthetic service storage, all live requests blocked."""
import json,os,sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'));sys.path.insert(0,str(ROOT/'tests'))
from app import execution_replay as replay,workbench,evaluation,model,tools,project
from test_interactive_expansion import ExpansionTests
report={'checks':[],'provider_requests':0,'live_api_requests':0,'errors':[]}
def passed(name):
 report['checks'].append(name);(ROOT/'outputs/improvement-batch3-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASS',name,flush=True)
fixture=ExpansionTests();fixture.setUp()
try:
 (fixture.root/'.env').write_text('MODEL_API_KEY=synthetic-ds\nMODEL_CALLS_ENABLED=false\nMODEL_BUDGET_CNY=99\nVISION_API_KEY=synthetic-vision\nVISION_CALLS_ENABLED=false\n')
 original=project.sources(fixture.rid)
 tools.execute(tools.ToolContext(fixture.rid,'task','original'),'project.list_files',{})
 app=FastAPI()
 for router in (replay.router,workbench.router,evaluation.router):app.include_router(router)
 with patch.object(model,'OpenAI',side_effect=AssertionError('provider forbidden')),patch.object(workbench,'local_command',return_value=False),TestClient(app) as client,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,channel=os.environ.get('PA_UI_BROWSER_CHANNEL') or None)
  try:
   page=browser.new_page(viewport={'width':1360,'height':1000});unexpected=[]
   page.on('pageerror',lambda error:report['errors'].append(str(error)))
   def route(r):
    request=r.request;url=urlparse(request.url)
    if url.netloc=='127.0.0.1:8793':return r.continue_()
    if url.netloc!='127.0.0.1:8000':unexpected.append(request.url);return r.abort()
    if request.method!='GET' and not (url.path.endswith('/setup/config') or url.path.endswith('/branches')):unexpected.append(request.method+' '+url.path);return r.abort()
    response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,headers={'Content-Type':'application/json'} if request.post_data_buffer else {})
    if response.status_code>=400:unexpected.append(str(response.status_code)+' '+url.path)
    r.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
   page.route('**/*',route)
   page.goto('http://127.0.0.1:8793/third.html?run_id='+fixture.rid);page.wait_for_load_state('networkidle')
   page.get_by_text('三个智能体的文字模型',exact=True).click()
   print('Role labels:',page.locator('label').all_text_contents(),flush=True)
   page.get_by_label('reviewer模型',exact=True).select_option('qwen3.8-flash')
   page.get_by_label('developer模型',exact=True).select_option('deepseek-v4-pro')
   expect(page.get_by_label('角色结果')).to_contain_text('qwen3.8-flash')
   page.get_by_label('developer模型',exact=True).select_option('')
   assert json.loads(page.get_by_label('角色结果').inner_text())=={'reviewer':'qwen3.8-flash'}
   passed('角色选择分别保存；恢复沿用默认不影响其他角色')
   page.get_by_role('button',name='测试：设置').click();page.wait_for_load_state('networkidle')
   page.get_by_label('向导配置服务').select_option('qwen_text')
   print('Setup labels:',page.locator('label').all_text_contents()[:12],flush=True)
   page.get_by_label('显式复用现有千问视觉密钥（北京地域）',exact=True).check()
   expect(page.get_by_label('我允许千问文字产生费用（同时受文字总许可限制）',exact=True)).not_to_be_checked()
   page.get_by_role('button',name='保存本地配置',exact=True).click()
   expect(page.get_by_text('已保存到本机配置。请先停止工作台，再重新启动；本次没有调用任何供应商。',exact=True)).to_be_visible()
   config=(fixture.root/'.env').read_text();assert 'QWEN_TEXT_USE_VISION_KEY=true' in config and 'QWEN_TEXT_CALLS_ENABLED=false' in config and 'VISION_CALLS_ENABLED=false' in config
   passed('千问密钥复用须显式选择；保存未开启文字或视觉许可')
   page.get_by_role('button',name='测试：回放').click();page.get_by_text('执行时间轴与固定决策分支',exact=True).click()
   page.get_by_role('button',name='读取执行记录',exact=True).click();page.get_by_role('button',name='绑定检查点、回执及原限额',exact=True).click()
   page.get_by_label('分支1步骤1').select_option('check_file');page.get_by_label('分支1检查1').select_option('contains');page.get_by_label('分支1预期1').fill('not-existing-unique')
   page.get_by_label('分支2步骤1').select_option('read_receipt')
   expect(page.get_by_label('分支2回执1')).to_have_value('original')
   page.get_by_role('button',name='在独立副本比较两个分支',exact=True).click()
   expect(page.get_by_text('源码断言未通过',exact=False)).to_be_visible()
   expect(page.get_by_text('源码断言：0/1 通过（不等于业务验收）',exact=True)).to_be_visible()
   passed('源码断言失败会停止该分支，另一分支可只读取冻结回执')
   link=page.get_by_role('link',name='导出脱敏分支记录');href=link.get_attribute('href')
   response=client.get(urlparse(href).path);assert response.status_code==200
   exported=response.json()['record'];assert exported['provider_calls']==0 and exported['branches'][1]['events'][0]['result']['tool']=='project.list_files'
   assert project.sources(fixture.rid)==original
   passed('导出包含固定输入和证据；原项目源码保持不变')
   page.screenshot(path=str(ROOT/'outputs/improvement-batch3-desktop.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844})
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
   page.screenshot(path=str(ROOT/'outputs/improvement-batch3-mobile.png'),full_page=True)
   assert not unexpected,unexpected
   assert not report['errors'],report['errors']
   passed('手机无横向溢出，无页面异常，无真实服务或供应商请求')
  finally:browser.close()
finally:fixture.tearDown()
