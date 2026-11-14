"""Offline real-browser failure -> rejected review -> scripted repair -> delivery.

Provider decisions are fixtures. This proves routing, not autonomous model skill.
Run from the repository with .venv/bin/python tests/integration/interface_feedback.py.
"""
import argparse, json, os, sys, uuid
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'))
os.environ.update(MODEL_CALLS_ENABLED='false',QWEN_TEXT_CALLS_ENABLED='false',VISION_CALLS_ENABLED='false')
from app import graph, executor, model, runner, store, project, tools, acceptance

parser=argparse.ArgumentParser();parser.add_argument('--report',default=str(ROOT/'outputs/offline-interface-feedback.json'));args=parser.parse_args()
report_path=Path(args.report).resolve();report_path.parent.mkdir(parents=True,exist_ok=True)
identifier=str(uuid.uuid4());calls=[]
app="""import {useState} from 'react'
export default function App(){const [items,setItems]=useState<string[]>(()=>JSON.parse(localStorage.getItem('interface-fixture')||'[]'));const [title,setTitle]=useState('');function save(next:string[]){setItems(next);localStorage.setItem('interface-fixture',JSON.stringify(next))}return <main><h1>控件反馈夹具</h1><label>任务标题<input value={title} onChange={e=>setTitle(e.target.value)}/></label><button onClick={()=>{if(title.trim()){save([...items,title.trim()]);setTitle('')}}}>添加任务</button><ul aria-label="任务列表">{items.map(item=><li key={item}><span>{item}</span><button onClick={()=>save(items.filter(x=>x!==item))}>删除</button></li>)}</ul></main>}
"""
suite="""import {test,expect} from '@playwright/test';test('生成测试只核对标题',async({page})=>{await page.goto('/');await expect(page.getByRole('heading',{name:'控件反馈夹具'})).toBeVisible()})"""
criteria=['输入标签任务标题，按钮添加任务；ul的aria-label为任务列表','数据只在localStorage，刷新后保留；每行有删除+标题按钮，删除后刷新仍不存在']
now=datetime.now(timezone.utc).isoformat()
state={'run_id':identifier,'runtime_version':3,'orchestration_version':1,'team_policy_version':1,'agent_protocol_version':3,'review_context_version':2,'requirements_confirmed':True,'product_spec':{'product_name':'控件反馈离线夹具','application_type':'frontend','page_details':[{'route':'/'}]},'acceptance_criteria':criteria,'acceptance_profile':'generated','user_request':'合成控件失败与修复回路，不调用供应商','created_at':now,'trace':[],'status':'running','repair_round':0,'repair_limit':1,'run_budget_cny':0,'model_call_limit':0,'execution_step_limit':40,'operator_assisted':True,'executor_done':True,'executor_tasks':[]}
store.create_run(identifier,state['user_request'],state,now);runner.initialize_workspace(identifier)
files=project.sources(identifier);files['src/App.tsx']=app;files['tests/interface.spec.ts']=suite;project.import_sources(identifier,files)

def provider(run_id,stage,system,user,schema,**kwargs):
    calls.append(stage);ctx=json.loads(user)
    if stage.startswith('agent-reviewer-'):
        episode,turn=map(int,stage.split('-')[-2:])
        if turn==1:
            if episode==0:
                assert not ctx['technical']['health_passed']
                assert '删除独立控件甲' in ctx['test_output']
            value={'action':'tool','summary':'读取当前实现','tool':'project.read_file','arguments':{'path':'src/App.tsx'}}
        else:
            gap=episode==0 and turn==3
            if gap:assert '当前验证' in ctx['feedback']
            value={'action':'submit','summary':'记录当前固定控件证据','review':{
                'verdict':'changes_requested' if gap else 'ready_for_manual',
                'summary':'固定检查失败：删除按钮必须为删除加完整标题，请修复可访问名称；不修改冻结检查。' if gap else '当前源码与固定检查齐备，人工验收另行确认。',
                'criteria':[{'index':i,'status':('gap' if gap else 'supported' if episode else 'manual') if i==1 else 'manual',
                    'evidence':'固定控件检查与已读取源码共同核对完整删除按钮名称；其余逐条人工确认。',
                    'files':['src/App.tsx'] if i==1 else []} for i in range(2)]}}
    elif stage.startswith('executor-'):
        turn=int(stage.split('-')[-1])
        assert ctx['interface_contract']['fixed_interface_checks']['checks']
        assert '删除按钮必须为删除加完整标题' in ctx['repair_failure']
        if turn==1:
            value={'action':'tool','summary':'按失败证据补完整删除名称','task_id':'repair-1','tool':'project.patch_file','arguments':{'path':'src/App.tsx','expected_sha256':project.digest(project.sources(identifier)['src/App.tsx']),'before':'<button onClick={()=>save(items.filter','after':'<button aria-label={"删除"+item} onClick={()=>save(items.filter'}}
        elif turn==2:value={'action':'complete_task','summary':'控件名称修复完成，交给固定检查','task_id':'repair-1'}
        else:raise AssertionError(stage)
    else:raise AssertionError(stage)
    return schema.model_validate(value),{}

with patch.object(model,'_client',side_effect=AssertionError('No remote calls')),patch.object(graph,'generate_json',side_effect=provider),patch.object(executor,'generate_json',side_effect=provider):
    flow=graph.build_graph(ROOT/'data/checkpoints.sqlite3');cfg={'configurable':{'thread_id':identifier},'recursion_limit':100}
    try:
        flow.update_state(cfg,state,as_node='generation');result=flow.invoke(None,cfg)
    finally:flow.checkpointer.conn.close()
assert result['status']=='completed' and result['repair_round']==1,result.get('last_error')
assert result['review_status']=='ready_for_manual'
assert any(e['stage']=='review' and e['status']=='rejected' for e in result['trace'])
assert any(e['stage']=='browser_test' and e['status']=='failed' for e in result['trace'])
assert acceptance.matrix(identifier)['technical']['health_passed']
assert project.sources(identifier)['tests/interface.spec.ts']==suite
report={'passed':True,'run_id':identifier,'paid_calls':0,'scripted_decisions':calls,'failed_contract_blocks_review':True,'failure_reaches_developer':True,'one_repair_round':True,'generated_test_unchanged':True,'artifacts':result['artifact_paths']}
report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
