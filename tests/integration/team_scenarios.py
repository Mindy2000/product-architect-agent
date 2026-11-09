"""Three-agent multipage, incremental and fullstack flows, fixed responses only."""
import json,os,sys,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import graph,executor,team,model,project,runner,store,designs

ROUTER=(ROOT/'templates/react/router.tsx').read_text()
APP="""import {useState} from 'react'
import {Router,Link} from './router'
function Home(){const [count,setCount]=useState(()=>Number(localStorage.getItem('closeout-count')||0));function save(value:number){setCount(value);localStorage.setItem('closeout-count',String(value))}return <main><h1>计数首页</h1><p>计数：{count}</p><button onClick={()=>save(count+1)}>增加</button>RESET<Link to="/about">说明</Link><Link to="/stats">统计</Link></main>}
export default function App(){return <Router routes={[{path:'/',element:<Home/>},{path:'/about',element:<main><h1>使用说明</h1><Link to="/">返回首页</Link></main>},{path:'/stats',element:<main><h1>统计页面</h1><Link to="/">返回首页</Link></main>}]}/>}
"""
SUITE="""import {test,expect} from '@playwright/test'
test('计数持久化与页面切换',async({page})=>{await page.goto('/');await page.getByRole('button',{name:'增加',exact:true}).click();await expect(page.getByText('计数：1')).toBeVisible();await page.reload();await expect(page.getByText('计数：1')).toBeVisible();await page.getByRole('link',{name:'说明',exact:true}).click();await expect(page.getByRole('heading',{name:'使用说明'})).toBeVisible();await page.getByRole('link',{name:'返回首页'}).click();await page.getByRole('link',{name:'统计',exact:true}).click();await expect(page.getByRole('heading',{name:'统计页面'})).toBeVisible()})
"""
RESET_TEST="""test('追加重置保留原计数键',async({page})=>{await page.goto('/');await page.getByRole('button',{name:'增加',exact:true}).click();await page.getByRole('button',{name:'重置',exact:true}).click();await page.reload();await expect(page.getByText('计数：0')).toBeVisible()})"""
CRUD="""import {useEffect,useState} from 'react'
import {api,type Row} from './pa-crud-client'
export default function App(){const [rows,setRows]=useState<Row[]>([]),[title,setTitle]=useState('');async function load(){setRows((await api.list('tasks')).items)}useEffect(()=>{void load()},[]);return <main><h1>任务管理</h1><label>任务标题<input value={title} onChange={e=>setTitle(e.target.value)}/></label><button onClick={()=>void api.create('tasks',{title}).then(load)}>新增</button><ul>{rows.map(row=><li key={row.id}>{String(row.title)}<button onClick={()=>void api.update('tasks',row.id,{title:'已更新'}).then(load)}>修改</button><button onClick={()=>void api.remove('tasks',row.id).then(load)}>删除</button></li>)}</ul></main>}
"""
CRUD_TEST="""import {test,expect} from '@playwright/test'
test('三智能体全栈CRUD',async({page})=>{await page.goto('/');await page.getByLabel('任务标题').fill('合成任务');await page.getByRole('button',{name:'新增',exact:true}).click();await expect(page.getByRole('listitem').filter({hasText:'合成任务'})).toBeVisible();await page.reload();await expect(page.getByRole('listitem').filter({hasText:'合成任务'})).toBeVisible();await page.getByRole('button',{name:'修改',exact:true}).click();await expect(page.getByRole('listitem').filter({hasText:'已更新'})).toBeVisible();await page.getByRole('button',{name:'删除',exact:true}).click();await expect(page.getByRole('listitem').filter({hasText:'已更新'})).toHaveCount(0)})
"""
SCHEMA={'version':1,'entities':[{'name':'tasks','label':'任务','fields':[{'name':'title','type':'text','required':True}]}]}

def spec(fullstack=False,incremental=False):
    pages=[('任务','/')] if fullstack else [('计数首页','/'),('使用说明','/about'),('统计页面','/stats')]
    value={'product_name':'协作收尾合成项目','summary':'仅固定响应，无供应商请求。','target_users':['合成测试用户'],'pages':[name for name,_ in pages],
           'page_details':[{'name':name,'route':route,'purpose':'核对既定功能','sections':['内容'],'interactions':['操作']} for name,route in pages],
           'core_features':['增删改任务','刷新保留'] if fullstack else ['计数与持久化','页面切换']+(['重置计数'] if incremental else []),
           'assumptions':[],'non_goals':['不使用远程服务'],'acceptance_criteria':['中文页面可用','操作与刷新持久化','页面和数据范围保持','交付可追溯'],
           'visual_direction':'简洁中文','data_notes':['SQLite' if fullstack else 'localStorage'],'application_type':'fullstack_crud' if fullstack else 'frontend'}
    if fullstack:value['crud_schema']=SCHEMA
    return designs.ProductDesign.model_validate(value).model_dump()

results=[]
def run_case(name,files,product,parent=None,step_limit=40,no_change=False):
    rid=str(uuid.uuid4());calls=[]
    state={'run_id':rid,'runtime_version':3,'orchestration_version':1,'team_policy_version':1,'requirements_confirmed':True,
           'product_spec':product,'acceptance_criteria':product['acceptance_criteria'],'user_request':name,'status':'queued','trace':[],
           'execution_step_limit':step_limit,'run_budget_cny':0,'model_call_limit':0,'repair_round':0,'repair_limit':3}
    if parent:state.update(parent_run_id=parent,change_request='保留原功能，追加重置计数')
    store.create_run(rid,name,state,'2026-11-09')
    def fake(identifier,stage,system,user,schema,**kw):
        assert identifier==rid;calls.append(stage);ctx=json.loads(user)
        if stage.startswith('agent-planner-'):
            return schema.model_validate({'action':'submit','summary':'分配所有冻结需求','plan':{'steps':[{'title':'实现功能','description':'实现冻结范围及测试','depends_on':[],'acceptance_indices':list(range(len(ctx['acceptance_criteria'])))},{'title':'阅读实现','description':'核对修改','depends_on':[1]},{'title':'保存版本','description':'保存受控源码','depends_on':[2]}]}}),{}
        if stage.startswith('agent-reviewer-'):
            turn=int(stage.rsplit('-',1)[1])
            if turn<=2:return schema(action='tool',summary='独立读取当前实现与测试',tool='project.read_file',arguments={'path':'src/App.tsx' if turn==1 else 'tests/e2e.spec.ts'}),{}
            ready=all(ctx['technical'][key] for key in ('build_passed','test_passed','health_passed','environment_verified'))
            verdict='blocked' if ctx.get('execution_incomplete') else 'changes_requested' if no_change else 'ready_for_manual'
            if not no_change:assert ready,ctx['failure']
            return schema(action='submit',summary='完成合成逐条审查',review=team.ReviewReport(verdict=verdict,summary='实际检查记录：开发未完成或缺口仍存在' if verdict!='ready_for_manual' else '实际技术检查通过，业务体验由用户确认',criteria=[team.CriterionReview(index=i,status='gap' if no_change else 'manual',evidence='合成审查发现构建错误需要修复' if no_change else '合成审查仅验证流程，真实业务另行验收') for i in range(len(ctx['acceptance_criteria']))])),{}
        turn=int(stage.rsplit('-',1)[1]);action='tool';task=None;tool=None;arguments={}
        if turn==1:
            old=project.sources(rid);changes=[]
            for path,content in files.items():
                if old.get(path)==content:continue
                changes.append({'operation':'replace' if path in old else 'create','path':path,'content':content,**({'expected_sha256':project.digest(old[path])} if path in old else {})})
            task='task-1';tool='project.apply_changes';arguments={'changes':changes}
        elif turn in (2,4,6):action='complete_task';task=f'task-{turn//2}'
        elif turn==3:task='task-2';tool='project.read_file';arguments={'path':'src/App.tsx'}
        elif turn==5:task='task-3';tool='git.snapshot'
        elif turn==7:action='finish'
        elif turn==8 and no_change:task='repair-1';tool='project.read_file';arguments={'path':'src/App.tsx'}
        elif turn==9 and no_change:action='complete_task';task='repair-1'
        elif turn==10 and no_change:action='finish'
        else:raise AssertionError('Unexpected decision '+stage)
        return model.ExecutorDecision(action=action,task_id=task,tool=tool,arguments=arguments,summary='合成动作 '+str(turn)),{}
    flow=graph.build_graph(ROOT/'data'/f'{rid}-checkpoint.sqlite3')
    try:
        with patch.object(graph,'generate_json',side_effect=fake),patch.object(executor,'generate_json',side_effect=fake),patch.object(model,'_client',side_effect=AssertionError('remote forbidden')):
            result=flow.invoke(state,{'configurable':{'thread_id':rid},'recursion_limit':256})
    finally:flow.checkpointer.conn.close()
    if no_change:
        assert result['status']=='failed_validation' and result['repair_round']==1,result
        assert result['review_status']=='stopped' and '没有变化' in result['review_stop_reason']
        assert len(calls)==14 and result['review_issues']
    elif step_limit==8:
        assert result['status']=='failed_validation' and result['review_reserve_reached'],result
        assert result['review_status']=='blocked' and len(calls)==8
    else:assert result['status']=='completed',result.get('last_error')
    assert len(result['plan_coverage'])==len(result['acceptance_criteria'])
    assert len(result['review_history'])==1 and len(calls)==len(set(calls))
    if not no_change and step_limit!=8:assert len(calls)==11
    with zipfile.ZipFile(ROOT/result['artifact_paths']['source_zip']) as z:
        exported=json.loads(z.read('AGENT_TEAM.json'));assert exported['plan_coverage']==result['plan_coverage']
    results.append({'name':name,'run_id':rid,'status':result['status'],'decisions':len(calls),'plan_coverage':len(result['plan_coverage']),'review_status':result['review_status']})
    print('PASS',name,flush=True);return rid

parent=run_case('三页面前端',{'src/App.tsx':APP.replace('RESET',''),'src/router.tsx':ROUTER,'tests/e2e.spec.ts':SUITE},spec())
original=project.manifest(parent)['digest']
run_case('已有项目增量维护',{'src/App.tsx':APP.replace('RESET','<button onClick={()=>save(0)}>重置</button>'),'src/router.tsx':ROUTER,'tests/e2e.spec.ts':SUITE+RESET_TEST},spec(incremental=True),parent)
assert project.manifest(parent)['digest']==original,'parent sources changed'
run_case('三智能体全栈CRUD',{'src/App.tsx':CRUD,'tests/e2e.spec.ts':CRUD_TEST},spec(fullstack=True))
run_case('无有效修改提前停止',{'src/App.tsx':APP.replace('RESET','')+"\nconst wrong: number = 'intentional failure'\n",'src/router.tsx':ROUTER,'tests/e2e.spec.ts':SUITE},spec(),no_change=True)
run_case('开发限额提前交给审查',{'src/App.tsx':APP.replace('RESET',''),'src/router.tsx':ROUTER,'tests/e2e.spec.ts':SUITE},spec(),step_limit=8)
(ROOT/'outputs/offline-team-scenarios.json').write_text(json.dumps({'passed':True,'provider_calls':0,'parent_unchanged':True,'cases':results},ensure_ascii=False,indent=2))
