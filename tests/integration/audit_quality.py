"""Exercise page coverage on synthetic source inside fixed containers, without a provider."""
import json,os,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import store,runner,project,tools,acceptance,model
model.generate_json=lambda *a,**k:(_ for _ in ()).throw(AssertionError('provider forbidden'))
id=str(uuid.uuid4())
state={'run_id':id,'status':'paused','runtime_version':2,'product_spec':{'application_type':'frontend','page_details':[{'route':'/'},{'route':'/settings'},{'route':'/items/:id'}]},'acceptance_profile':'generated','acceptance_criteria':['空数据提示','非法输入错误及恢复','刷新后持久化','第二页面可用'],'trace':[]}
store.create_run(id,'synthetic offline coverage',state,'offline')
runner.initialize_workspace(id)
APP="""import {useState} from 'react'
export default function App(){const [items,setItems]=useState<string[]>(()=>JSON.parse(localStorage.getItem('items')||'[]'));const [text,setText]=useState('');const [error,setError]=useState('');if(location.hash==='#/settings')return <main style={{padding:12}}><h1>设置</h1><button>本地选项</button></main>;return <main><h1>离线覆盖</h1>{!items.length&&<p>暂无记录</p>}<label>内容<input value={text} onChange={e=>setText(e.target.value)}/></label><button onClick={()=>{if(!text.trim()){setError('请输入内容');return}const next=[...items,text];setItems(next);localStorage.setItem('items',JSON.stringify(next));setText('');setError('')}}>保存</button>{error&&<p role="alert">{error}</p>}<ul>{items.map((item,i)=><li key={i}>{item}</li>)}</ul></main>}
"""
SUITE="""import {test,expect} from '@playwright/test'
test('空数据、输入错误与恢复、刷新持久化、键盘提交',async({page})=>{await page.goto('/');await expect(page.getByText('暂无记录')).toBeVisible();await page.getByRole('button',{name:'保存'}).click();await expect(page.getByRole('alert')).toHaveText('请输入内容');await page.getByLabel('内容').fill('合成记录');await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'保存'})).toBeFocused();await page.keyboard.press('Enter');await expect(page.getByRole('alert')).toHaveCount(0);await expect(page.getByRole('listitem')).toHaveText('合成记录');await page.reload();await expect(page.getByRole('listitem')).toHaveText('合成记录')})
"""
files={k:runner.BASE_FILES[k] for k in project.REQUIRED_FILES};files.update({'src/App.tsx':APP,'tests/e2e.spec.ts':SUITE});project.import_sources(id,files)
results=[]
def execute(kind,operation):
 result=tools.execute(tools.ToolContext(id,'quality',operation),'project.'+kind,{})
 if not result.get('ok'):raise AssertionError(result)
 return result['data']
assert execute('build','healthy-build')['passed']
healthy=execute('test','healthy-test');assert healthy['passed'],healthy['output']
assert healthy['coverage']['routes']==['/','/#/settings'];assert len(healthy['coverage']['pending_routes'])==1
matrix=acceptance.matrix(id);assert matrix['counts']['passed']==0 and matrix['technical']['health_passed']
results.append('static routes desktop/mobile, reload and keyboard; synthetic empty/error/persistence; dynamic routes remain pending')
files['src/App.tsx']=APP.replace('padding:12','minWidth:1800');project.import_sources(id,files)
assert execute('build','broken-build')['passed']
broken=execute('test','broken-test');assert not broken['passed'];assert not broken['health_passed'];assert '/#/settings' in broken['output']
results.append('secondary-route overflow rejected even when root business checks pass')
files['src/App.tsx']=APP;project.import_sources(id,files)
assert execute('build','fixed-build')['passed'];assert execute('test','fixed-test')['passed']
results.append('fix restores current evidence; no user acceptance automatically marked')
(ROOT/'outputs/offline-audit-quality.json').write_text(json.dumps({'provider_calls':0,'checks':results,'passed':True},ensure_ascii=False,indent=2))
print(json.dumps({'checks':results,'passed':True},ensure_ascii=False),flush=True)
