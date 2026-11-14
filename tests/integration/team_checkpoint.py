"""Three specialists with a lost reviewer checkpoint and durable tool/model replay, with deterministic provider replacement."""
import argparse,hashlib,json,os,sys,time,uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'))
parser=argparse.ArgumentParser()
for flag in ['protocol-repair','coverage-repair','final-closeout','review-submission','review-submission-fail']:parser.add_argument('--'+flag,action='store_true')
parser.add_argument('--report');args=parser.parse_args()
def save_report(report,default):
    path=Path(args.report).resolve() if args.report else ROOT/default
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
final_invalid_mode=args.review_submission_fail
submission_mode='--review-submission' in sys.argv or final_invalid_mode
closeout_mode='--final-closeout' in sys.argv or submission_mode
coverage_mode='--coverage-repair' in sys.argv or closeout_mode
protocol_mode='--protocol-repair' in sys.argv or coverage_mode
os.environ.update(MODEL_CALLS_ENABLED='false',QWEN_TEXT_CALLS_ENABLED='false',VISION_CALLS_ENABLED='false')
from app import graph,executor,runner,project,store,tools,model,research,team
from app.designs import ProductDesign
from dataclasses import replace
_original_test=tools.TOOLS['project.test'].handler
def traced_test(*args,**kwargs):
    try:return _original_test(*args,**kwargs)
    except Exception:
        import traceback;traceback.print_exc();raise
tools.TOOLS['project.test']=replace(tools.TOOLS['project.test'],handler=traced_test)
from langgraph.types import Command
lab=ROOT/'data'/('offline-team-protocol-' if protocol_mode else 'offline-team-')/uuid.uuid4().hex[:10];lab.mkdir(parents=True)
identifier=str(uuid.uuid4());calls=[];pause_once=[False]
app="""import {useState} from 'react'
export default function App(){const [count,setCount]=useState(()=>Number(localStorage.getItem('offline-count')||0));return <main><h1>执行器验收</h1><p>计数：{count}</p><button onClick={()=>{localStorage.setItem('offline-count',String(count+1));setCount(count+1)}}>增加</button></main>}
"""
suite="""import {test,expect} from '@playwright/test'
test('操作与刷新持久化',async({page})=>{await page.goto('/');await page.getByRole('button',{name:'增加'}).click();await expect(page.getByText('计数：1')).toBeVisible();await page.reload();await expect(page.getByText('计数：1')).toBeVisible()})
"""
spec=ProductDesign.model_validate({'product_name':'M19 执行器合成任务','summary':'验证新版结构化工具、暂停恢复和交付，模型用固定响应代替。','target_users':['验收人员'],'pages':['计数'],'page_details':[{'name':'计数','route':'/','purpose':'验证状态','sections':['计数'],'interactions':['增加']}],'core_features':['增加计数','刷新保留'],'assumptions':[],'non_goals':['不使用远程API'],'acceptance_criteria':['显示中文标题','按钮增加计数','刷新保留计数','输出源码和Trace'],'visual_direction':'本地简洁界面','data_notes':['localStorage 合成数据']}).model_dump()
def scripted_provider(run_id,stage,*args,**kwargs):
    calls.append(stage)
    if stage.startswith('agent-reviewer-'):
        turn=int(stage.rsplit('-',1)[1]);ctx=json.loads(args[1])
        if coverage_mode and turn>1:
            if turn<=8:
                path={2:'tests/e2e.spec.ts',3:'src/style.css'}.get(turn,'src/style.css' if closeout_mode else 'src/App.tsx')
                return team.EvidenceReviewerAction(action='tool',summary='合成场景消耗读取步骤以核对末两步提交保护',tool='project.read_file',arguments={'path':path}),{}
            assert args[2] is team.FinalReviewerAction, 'last two turns must use submit-only schema'
            if closeout_mode:
                assert ctx['tools']==[]
                evidence=next(x for x in ctx['source_evidence']['files'] if x['path']=='src/App.tsx')
                assert evidence['full_file_present'] and evidence['segments'][0]['content']==app
            if submission_mode:
                assert ctx['submission_contract']['read_tools_available'] is False
                assert 'src/App.tsx' in ctx['submission_contract']['citable_files']
                assert ctx['submission_contract']['remaining_steps']==11-turn
                if turn==10:assert ctx['feedback_details']['code']=='review_output_format'
                if turn==9 or final_invalid_mode:
                    try:model.parse_model_json('[{"action":"stop","summary":"错误数组夹具"}]',args[2])
                    except ValueError as error:raise model.ModelOutputInvalid(str(error),{})
                    raise AssertionError('bare array was accepted')
            if turn==10 and not submission_mode:assert 'tests/standard/health.spec.ts' in ctx['feedback_details']['unknown_files']
            return team.EvidenceReviewerAction(action='submit',summary='末两步结论校验与纠正',review=team.EvidenceReviewReport(verdict='ready_for_manual',summary='合成流程核对当前证据与拒绝后纠正，业务人工验收另行记录',criteria=[team.EvidenceCriterionReview(index=i,status='supported' if i==0 else 'manual',evidence='已读取当前实现和浏览器测试' if i==0 else '合成场景不代替业务人工验收',files=(['tests/standard/health.spec.ts'] if turn==9 else ['src/App.tsx','tests/e2e.spec.ts']) if i==0 else [],fixed_checks=['build','page_health'] if i==0 and turn==10 else []) for i in range(len(ctx['acceptance_criteria']))])),{}
        if turn==1:return team.CloseoutDecision(action='tool',summary='读取实现进行独立审查',tool='project.read_file',arguments={'path':'src/App.tsx'}),{}
        if protocol_mode:
            if turn==3:
                assert ctx['feedback_details']['read_next']==['tests/e2e.spec.ts']
                return team.CloseoutDecision(action='tool',summary='根据退回信息补读测试',tool='project.read_file',arguments={'path':'tests/e2e.spec.ts'}),{}
            if turn==4:assert 'tests/e2e.spec.ts' in ctx['review_packet']['current_read_files']
            return team.CloseoutDecision(action='submit',summary='提交有证据的逐条审查',review=team.ReviewReport(verdict='ready_for_manual',summary='源码与固定交互检查齐备，人工体验另行确认',criteria=[team.CriterionReview(index=i,status='supported' if i==0 else 'manual',evidence='标题实现和浏览器测试已读取' if i==0 else '仍需人工核对实际体验',files=['src/App.tsx','tests/e2e.spec.ts'] if i==0 else []) for i in range(len(ctx['acceptance_criteria']))])),{}
        return team.CloseoutDecision(action='submit',summary='逐条审查完成',review=team.ReviewReport(verdict='ready_for_manual',summary='技术证据与源码已读取，人工验收另行记录',criteria=[team.CriterionReview(index=i,status='manual',evidence='合成审查完成，仍由用户确认实际体验') for i in range(len(ctx['acceptance_criteria']))])),{}
    if stage.startswith('agent-planner-'):
        if closeout_mode:
            return team.FocusedPlannerAction(action='submit',summary='单项完整功能包含来源与测试',plan=model.FocusedPlanResult(steps=[model.PlanStep(title='完整计数页面与测试',description='一起实现标题、交互、持久化与测试，不拆分检查任务',depends_on=[],acceptance_indices=list(range(6)))])),{}
        return team.CloseoutDecision(action='submit',summary='规划开发任务',plan=model.PlanResult.model_validate({'steps':[{'title':'实现状态与测试','description':'写入计数器及浏览器检查','depends_on':[],'acceptance_indices':list(range(6))},{'title':'检查源码清单','description':'查看受控文件','depends_on':[1],'acceptance_indices':[]},{'title':'保存源码版本','description':'固定Git快照','depends_on':[2],'acceptance_indices':[3]}]})),{}
    if stage=='diagnosis':return model.RepairDecision(action='stop',reason='离线夹具不自动掩盖新失败，保留真实诊断'),{}
    turn=int(stage.split('-')[-1]);action='tool';task=None;tool=None;arguments={}
    if turn==1:
        task='task-1';tool='project.apply_changes';old=project.sources(identifier)
        arguments={'changes':[{'operation':'replace','path':'src/App.tsx','expected_sha256':project.digest(old['src/App.tsx']),'content':app.replace('>增加<','>待修复<') if coverage_mode else app},{'operation':'create','path':'tests/e2e.spec.ts','content':suite}]}
    elif closeout_mode and turn==2:
        task='task-1';tool='project.patch_file';arguments={'path':'src/App.tsx','expected_sha256':project.digest(project.sources(identifier)['src/App.tsx']),'before':'>待修复<','after':'>增加<'}
    elif closeout_mode and turn==3:action='complete_task';task='task-1'
    elif turn in {2,4,6}:action='complete_task';task='task-'+str(turn//2)
    elif turn==3:
        task='task-2';tool='project.patch_file' if coverage_mode else 'project.list_files'
        if coverage_mode:arguments={'path':'src/App.tsx','expected_sha256':project.digest(project.sources(identifier)['src/App.tsx']),'before':'>待修复<','after':'>增加<'}
    elif turn==5:task='task-3';tool='git.snapshot'
    elif turn==7:action='finish'
    else:raise AssertionError('unexpected extra decision '+stage)
    return (model.CoverageExecutorDecision if coverage_mode else model.ExecutorDecision)(action=action,task_id=task,tool=tool,arguments=arguments,summary='确定性离线验收动作 '+str(turn)),{}

def provider(run_id,stage,*args,**kwargs):
    value,usage=scripted_provider(run_id,stage,*args,**kwargs)
    if protocol_mode:
        schema=args[2];raw=value.model_dump()
        shapes=schema.model_json_schema().get('oneOf',[])
        branch=next((b for b in shapes if b['properties']['action']['const']==raw.get('action')),None)
        if branch:raw={key:raw[key] for key in branch['properties']}
        value=model.parse_model_json(json.dumps(raw,ensure_ascii=False),schema)
    return value,usage

with ExitStack() as stack:
    for module,name,value in [(runner,'ROOT',lab),(runner,'GENERATED_ROOT',lab/'generated-projects'),(runner,'OUTPUT_ROOT',lab/'outputs'),(store,'DATA_DIR',lab/'data'),(store,'RUNS_DB',lab/'data/runs.sqlite3'),(model,'DATA_DIR',lab/'data'),(model,'USAGE_DB',lab/'data/usage.sqlite3'),(research,'DATA',lab/'data/research')]:stack.enter_context(patch.object(module,name,value))
    stack.enter_context(patch.object(graph,'generate_json',side_effect=provider));stack.enter_context(patch.object(executor,'generate_json',side_effect=provider))
    stack.enter_context(patch.object(model,'_client',side_effect=AssertionError('offline test: provider access forbidden')))
    execute=tools.execute
    def traced(context,name,arguments):
        receipt=execute(context,name,arguments)
        if name=='project.apply_changes' and receipt['ok'] and not pause_once[0]:
            pause_once[0]=True;store.set_pause_requested(identifier,True)
        return receipt
    stack.enter_context(patch.object(tools,'execute',side_effect=traced))
    initial={'run_id':identifier,'runtime_version':3,'orchestration_version':1,'team_policy_version':1,'requirements_confirmed':True,'product_spec':spec,'acceptance_criteria':spec['acceptance_criteria'],'user_request':'M19 合成执行器任务','trace':[],'repair_round':0,'run_budget_cny':0,'model_call_limit':0,'execution_step_limit':24,'status':'queued'}
    if protocol_mode:initial.update(agent_protocol_version=3 if closeout_mode else 2 if coverage_mode else 1,review_context_version=2,role_models={'planner':'deepseek-flash','developer':'deepseek-flash','reviewer':'qwen3.8-flash'})
    store.create_run(identifier,initial['user_request'],initial,'2026-11-03')
    checkpoint=lab/'data/checkpoints.sqlite3';config={'configurable':{'thread_id':identifier},'recursion_limit':256}
    workflow=graph.build_graph(checkpoint)
    first=workflow.invoke(initial,config)
    assert '__interrupt__' in first and store.get_run(identifier)['status']=='paused'
    assert calls==['agent-planner-0-1','executor-1'],calls
    first_digest=project.manifest(identifier)['digest'];workflow.checkpointer.conn.close()
    store.set_pause_requested(identifier,False);workflow=graph.build_graph(checkpoint)
    original_step=team.step
    def interrupted_step(state,role,provider):
        value=original_step(state,role,provider)
        if role=='reviewer' and value['turn']==1:raise model.ModelCallFailed('synthetic crash after durable reviewer read')
        return value
    with patch.object(team,'step',side_effect=interrupted_step):
        try:workflow.invoke(Command(resume=True),config)
        except model.ModelCallFailed:pass
        else:raise AssertionError('reviewer crash not reached')
    assert calls[-1]=='agent-reviewer-0-1',calls
    workflow.checkpointer.conn.close();workflow=graph.build_graph(checkpoint)
    try:result=workflow.invoke(None,config)
    finally:workflow.checkpointer.conn.close()
    expected_steps=14 if closeout_mode else 18 if coverage_mode else 12 if protocol_mode else 10
    if final_invalid_mode:
        assert result['status']=='failed_validation' and result['review_status']=='stopped',result
        assert '审查最后提交未通过' in result['last_error']
        assert '顶层必须是单个JSON对象' in result['last_error']
        assert result['team_steps']==14 and len(calls)==14 and len(set(calls))==14
        assert result['specialist_feedback_details']['code']=='review_output_format'
        assert not any(x['operation_id'] in {'reviewer_0_9','reviewer_0_10'} for x in tools.history(identifier,100))
        report={'passed':True,'scenario':'two_invalid_final_submissions_stop','paid_calls':0,'run_id':identifier,
                'lab_root':str(lab),'scripted_calls':len(calls),'no_extra_calls':True,'last_error_preserved':True,
                'status':result['status'],'artifacts':result['artifact_paths']}
        save_report(report,'outputs/offline-review-submission-stop.json');sys.exit(0)
    assert result['review_status']=='ready_for_manual' and result['team_steps']==expected_steps,result
    assert len(result['agent_handoffs'])==4,result['agent_handoffs']
    assert len([item for item in tools.history(identifier,100) if item['operation_id']=='reviewer_0_1'])==1
    assert any(e.get('agent_id')=='reviewer' and e.get('replayed') for e in result['trace'])
    assert result['status']=='completed',result.get('last_error')
    assert len(calls)==len(set(calls)) and len(calls)==expected_steps,calls
    if protocol_mode:
        assert result['agent_protocol_version']==(3 if closeout_mode else 2 if coverage_mode else 1)
        assert any(event['status']=='rejected' and event['stage']=='review' for event in result['trace'])
        assert not result['specialist_feedback_details']
    assert (project.manifest(identifier)['digest']!=first_digest if coverage_mode else project.manifest(identifier)['digest']==first_digest),'unexpected source changes after resume'
    for key in ['source_zip','trace','git_diff','manifest','report']:assert (lab/result['artifact_paths'][key]).is_file(),key
    report={'passed':True,'provider':'deterministic_fake_no_network','paid_calls':0,'lab_root':str(lab),'run_id':identifier,'orchestration_version':1,'team_policy_version':1,'reviewer_crash_replayed_without_provider_or_tool_repeat':True,'shared_steps':result['team_steps'],'handoffs':len(result['agent_handoffs']),'pause_after_first_edit':True,'recreated_checkpoint_resume':True,'duplicate_provider_decisions':False,'scripted_calls':calls,'status':result['status'],'build_status':result['build_status'],'test_status':result['test_status'],'executor_turns':result['executor_turns'],'repair_round':result['repair_round'],'source_digest_unchanged_after_resume':not coverage_mode,'explicit_flat_patch_applied':coverage_mode,'artifacts':result['artifact_paths']}
    if closeout_mode:
        assert result['executor_turns']==3 and result['executor_done'] and not result.get('executor_stopped')
        report.update(single_complete_task_auto_submitted=True,early_source_body_retained=True,final_tool_catalog_empty=True)
    report.update(agent_protocol_version=result.get('agent_protocol_version'),evidence_rejection_corrected=protocol_mode)
    if submission_mode:report.update(final_array_rejected_then_corrected=True,explicit_citation_contract=True)
    save_report(report,'outputs/offline-review-submission.json' if submission_mode else 'outputs/offline-final-closeout.json' if closeout_mode else 'outputs/offline-coverage-repair.json' if coverage_mode else 'outputs/offline-protocol-repair.json' if protocol_mode else 'outputs/offline-team.json')
