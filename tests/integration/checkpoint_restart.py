"""Real v2 executor/tools/checkpoint/container delivery, with deterministic provider replacement."""
import hashlib,json,os,sys,time,uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import graph,executor,runner,project,store,tools,model,research
from app.designs import ProductDesign
from langgraph.types import Command
lab=ROOT/'data'/('offline-graph-'+uuid.uuid4().hex[:10]);lab.mkdir()
identifier=str(uuid.uuid4());calls=[];pause_once=[False]
app="""import {useState} from 'react'
export default function App(){const [count,setCount]=useState(()=>Number(localStorage.getItem('offline-count')||0));return <main><h1>执行器验收</h1><p>计数：{count}</p><button onClick={()=>{localStorage.setItem('offline-count',String(count+1));setCount(count+1)}}>增加</button></main>}
"""
suite="""import {test,expect} from '@playwright/test'
test('操作与刷新持久化',async({page})=>{await page.goto('/');await page.getByRole('button',{name:'增加'}).click();await expect(page.getByText('计数：1')).toBeVisible();await page.reload();await expect(page.getByText('计数：1')).toBeVisible()})
"""
spec=ProductDesign.model_validate({'product_name':'M19 执行器合成任务','summary':'验证新版结构化工具、暂停恢复和交付，模型用固定响应代替。','target_users':['验收人员'],'pages':['计数'],'page_details':[{'name':'计数','route':'/','purpose':'验证状态','sections':['计数'],'interactions':['增加']}],'core_features':['增加计数','刷新保留'],'assumptions':[],'non_goals':['不使用远程API'],'acceptance_criteria':['显示中文标题','按钮增加计数','刷新保留计数','输出源码和Trace'],'visual_direction':'本地简洁界面','data_notes':['localStorage 合成数据']}).model_dump()
def provider(run_id,stage,*args,**kwargs):
    calls.append(stage)
    if stage=='planning':
        return model.PlanResult.model_validate({'steps':[{'title':'实现状态与测试','description':'写入计数器及浏览器检查','depends_on':[],'acceptance_indices':[0,1,2]},{'title':'检查源码清单','description':'查看受控文件','depends_on':[1],'acceptance_indices':[]},{'title':'保存源码版本','description':'固定Git快照','depends_on':[2],'acceptance_indices':[3]}]}),{}
    turn=int(stage.split('-')[-1]);action='tool';task=None;tool=None;arguments={}
    if turn==1:
        task='task-1';tool='project.apply_changes';old=project.sources(identifier)
        arguments={'changes':[{'operation':'replace','path':'src/App.tsx','expected_sha256':project.digest(old['src/App.tsx']),'content':app},{'operation':'create','path':'tests/e2e.spec.ts','content':suite}]}
    elif turn in {2,4,6}:action='complete_task';task='task-'+str(turn//2)
    elif turn==3:task='task-2';tool='project.list_files'
    elif turn==5:task='task-3';tool='git.snapshot'
    elif turn==7:action='finish'
    else:raise AssertionError('unexpected extra decision '+stage)
    return model.ExecutorDecision(action=action,task_id=task,tool=tool,arguments=arguments,summary='确定性离线验收动作 '+str(turn)),{}

with ExitStack() as stack:
    for module,name,value in [(runner,'ROOT',lab),(runner,'GENERATED_ROOT',lab/'generated-projects'),(runner,'OUTPUT_ROOT',lab/'outputs'),(store,'DATA_DIR',lab/'data'),(store,'RUNS_DB',lab/'data/runs.sqlite3'),(model,'DATA_DIR',lab/'data'),(model,'USAGE_DB',lab/'data/usage.sqlite3'),(research,'DATA',lab/'data/research')]:stack.enter_context(patch.object(module,name,value))
    stack.enter_context(patch.object(graph,'generate_json',side_effect=provider));stack.enter_context(patch.object(executor,'generate_json',side_effect=provider))
    execute=tools.execute
    def traced(context,name,arguments):
        receipt=execute(context,name,arguments)
        if name=='project.apply_changes' and receipt['ok'] and not pause_once[0]:
            pause_once[0]=True;store.set_pause_requested(identifier,True)
        return receipt
    stack.enter_context(patch.object(tools,'execute',side_effect=traced))
    initial={'run_id':identifier,'runtime_version':2,'requirements_confirmed':True,'product_spec':spec,'acceptance_criteria':spec['acceptance_criteria'],'user_request':'M19 合成执行器任务','trace':[],'repair_round':0,'run_budget_cny':0,'model_call_limit':0,'execution_step_limit':24,'status':'queued'}
    store.create_run(identifier,initial['user_request'],initial,'2026-11-03')
    checkpoint=lab/'data/checkpoints.sqlite3';config={'configurable':{'thread_id':identifier},'recursion_limit':256}
    workflow=graph.build_graph(checkpoint)
    first=workflow.invoke(initial,config)
    assert '__interrupt__' in first and store.get_run(identifier)['status']=='paused'
    assert calls==['planning','executor-1'],calls
    first_digest=project.manifest(identifier)['digest'];workflow.checkpointer.conn.close()
    store.set_pause_requested(identifier,False);workflow=graph.build_graph(checkpoint)
    try:result=workflow.invoke(Command(resume=True),config)
    finally:workflow.checkpointer.conn.close()
    assert result['status']=='completed',result.get('last_error')
    assert len(calls)==len(set(calls)) and len(calls)==8,calls
    assert project.manifest(identifier)['digest']==first_digest,'resume unexpectedly rewrote sources'
    for key in ['source_zip','trace','git_diff','manifest','report']:assert (lab/result['artifact_paths'][key]).is_file(),key
    report={'passed':True,'provider':'deterministic_fake_no_network','paid_calls':0,'lab_root':str(lab),'run_id':identifier,'pause_after_first_edit':True,'recreated_checkpoint_resume':True,'duplicate_provider_decisions':False,'scripted_calls':calls,'status':result['status'],'build_status':result['build_status'],'test_status':result['test_status'],'executor_turns':result['executor_turns'],'repair_round':result['repair_round'],'source_digest_unchanged_after_resume':True,'artifacts':result['artifact_paths']}
    (ROOT/'outputs/offline-graph.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
