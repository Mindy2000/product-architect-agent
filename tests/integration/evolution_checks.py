"""Additional UI and API checks invoked inside the credential-free expansion fixture."""
import json,uuid
from pathlib import Path
from playwright.sync_api import expect

def run(page,rid,api,root,check):
 base=api+'/collaboration/projects/'+rid
 before=page.request.get(api+'/runs/'+rid).json()
 # Stable desktop and mobile frames use isolated storage, preserving interactive preview data.
 visual=page.locator('details').filter(has=page.locator('summary').get_by_text('视觉对照设置',exact=True)).last
 page.locator('summary').get_by_text('视觉对照设置',exact=True).click()
 page.get_by_label('开启独立视觉模式').check();page.get_by_label('视口',exact=True).select_option('mobile')
 with page.expect_response(lambda r:r.url.endswith('/preview/action') and r.request.method=='POST') as configured:page.get_by_role('button',name='应用设置并刷新截图').click()
 frame=configured.value.json();assert configured.value.status==200,frame;assert (frame['width'],frame['height'])==(390,844);assert frame['visual_settings']['enabled']
 page.get_by_label('基线名称').fill('手机独立空状态');page.get_by_role('button',name='确认当前页面为新基线').click();page.get_by_label('选择比较基线').select_option(label='手机独立空状态 · /')
 with page.expect_response(lambda r:r.url.endswith('/compare') and r.request.method=='POST') as compared:page.get_by_role('button',name='与当前页面比较').click()
 assert compared.value.status==200,compared.value.json();assert compared.value.json()['changed_ratio']==0
 page.locator('.interactive-preview').screenshot(path=str(root/'outputs/offline-evolution-mobile-preview.png'))
 import base64,io
 from PIL import Image
 masked=page.request.post(api+'/runs/'+rid+'/preview/action',data={'action':'configure','visual':{**frame['visual_settings'],'masks':[{'x':0,'y':0,'width':20,'height':20}],'region':{'x':0,'y':0,'width':100,'height':100}}})
 assert masked.status==200,masked.json()
 with Image.open(io.BytesIO(base64.b64decode(masked.json()['image']))) as screenshot:assert screenshot.convert('RGB').getpixel((1,1))==(0,0,0)
 page.get_by_label('开启独立视觉模式').uncheck();page.get_by_label('视口',exact=True).select_option('desktop')
 with page.expect_response(lambda r:r.url.endswith('/preview/action')) as restored:page.get_by_role('button',name='应用设置并刷新截图').click()
 assert restored.value.status==200
 check('手机固定数据视觉模式、基线零差异、返回原交互数据')
 # Unreferenced records can be archived and safely deleted via a fresh impact preview.
 from app import page_feedback as pf
 n=pf.add_note(uuid.UUID(rid),pf.NoteInput(comment='用于生命周期测试的临时意见',comparison_id=page.request.get(base+'/evidence').json()['comparisons'][0]['id']))
 page.locator('summary').get_by_text('资料管理与意见闭环',exact=True).click();page.get_by_label('搜索协作资料').fill('生命周期测试')
 card=page.locator('.rule-card').filter(has=page.get_by_text('页面意见 · 用于生命周期测试的临时意见',exact=True));expect(card).to_be_visible();card.get_by_role('button',name='推进或重新打开意见').click();page.get_by_label('意见状态说明').fill('合成意见确认采用');page.get_by_role('button',name='保存状态',exact=True).click();expect(card.locator('small')).to_contain_text('已采用')
 card.get_by_role('button',name='归档',exact=True).click();expect(card).to_have_count(0);page.get_by_label('查看已归档').check();expect(card).to_be_visible()
 card.get_by_role('button',name='查看删除影响').click();expect(page.get_by_role('button',name='确认删除这条记录')).to_be_enabled();page.get_by_role('button',name='确认删除这条记录').click();expect(card).to_have_count(0)
 page.get_by_label('查看已归档').uncheck();page.get_by_label('搜索协作资料').fill('');check('意见采用、归档搜索、引用预览后删除和图片清理')
 # Template preview and explicit creation do not invoke any provider.
 page.locator('summary').get_by_text('另存为可复用模板',exact=True).click();page.get_by_role('button',name='读取候选内容').click();expect(page.get_by_label('模板名称',exact=True)).to_have_value('任务管理');page.get_by_label('模板名称',exact=True).fill('合成可复用任务模板')
 page.get_by_role('button',name='预览将保存的全部内容').click();expect(page.get_by_role('button',name='我已检查内容，确认保存模板')).to_be_visible()
 with page.expect_response(lambda r:r.url.endswith('/custom-templates') and r.request.method=='POST') as saved:page.get_by_role('button',name='我已检查内容，确认保存模板').click()
 assert saved.value.status==200,saved.value.json();template=saved.value.json();draft=page.request.post(api+'/templates/custom-'+template['id']+'/draft',data={});assert draft.status==200,draft.json();assert draft.json()['budget']['calls']==0
 # Seed custom source in a separate project without overwriting the source project.
 from app import store,runner,project,capability_templates as templates
 child=str(uuid.uuid4());state={**before,'run_id':child,'status':'paused','template_id':'custom-'+template['id'],'template_digest':template['digest'],'parent_run_id':None};store.create_run(child,'custom fixture',state,'now');runner.initialize_workspace(child);templates.seed(state)
 assert project.sources(child)==project.sources(rid)
 check('自定义模板逐文件预览、确认保存、无模型方案及独立源码复用')
 # Timeline + controlled branches; original sources and checkpoints never change.
 page.locator('summary').get_by_text('执行时间轴与固定决策分支',exact=True).click();page.get_by_role('button',name='读取执行记录',exact=True).click();expect(page.get_by_label('源码回放位置',exact=True)).to_be_visible()
 page.get_by_role('button',name='绑定检查点、回执及原限额').click();page.get_by_text('高级：固定决策 JSON',exact=True).click();expect(page.get_by_label('两个固定决策分支（JSON）')).to_be_visible()
 with page.expect_response(lambda r:r.url.endswith('/branches') and r.request.method=='POST') as branched:page.get_by_role('button',name='在独立副本比较两个分支').click()
 b=branched.value.json();assert branched.value.status==200,b;assert b['provider_calls']==0 and b['same_source'];assert b['branches'][0]['stop_reason']!=b['branches'][1]['stop_reason'];assert b['basis']['checkpoint_match']
 after=page.request.get(api+'/runs/'+rid).json();assert before['team_steps']==after['team_steps'];assert before['status']==after['status'];check('实际执行时间轴、checkpoint关联、固定分支不同停止结果且原任务不变')
 page.locator('details').filter(has=page.locator('summary').get_by_text('执行时间轴与固定决策分支',exact=True)).last.screenshot(path=str(root/'outputs/offline-evolution-replay.png'))
 # Scope selection and conflict detection are deterministic; no free-text semantic inference.
 def rule(value):
  response=page.request.post(base+'/rules',data={'title':'首页字号'+value,'content':'首页字号固定'+value,'scope_type':'page','targets':['/'],'constraints':{'font_size':value}});assert response.status==200,response.json();return response.json()
 a=rule('16');rule('18');choice=page.request.post(base+'/rules/selection',data={'page':['/']});assert any(x['kind']=='conflict' for x in choice.json()['warnings'])
 selected=page.request.post(base+'/rules/selection',data={'page':['/'],'rule_ids':[a['id']]});assert not selected.json()['warnings'];check('指定页面规则精确选择、明确键值冲突提示')
 page.locator('summary').get_by_text('资料管理与意见闭环',exact=True).click()
