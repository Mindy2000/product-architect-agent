"""Fixed-response evaluation fixture, executed only in a credential-free disposable copy."""
import json,os,sys,time,uuid
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from . import runner,store,graph,executor,model,team,project,capability_templates as templates,container_owner,runtime_identity

def main():
    identifier=str(uuid.UUID(sys.argv[1]));container_owner.LABELS.extend(['--label','pa.maintenance='+identifier]);rows=[]
    for limit in (40,8):
        for template_id in templates.IDS:
            t=templates.load(template_id);rid=str(uuid.uuid4());started=time.monotonic()
            state={'run_id':rid,'runtime_version':3,'orchestration_version':1,'team_policy_version':1,'collaboration_version':1,'requirements_confirmed':True,'product_spec':t['spec'],'acceptance_criteria':t['spec']['acceptance_criteria'],'user_request':'离线合成评测','status':'queued','trace':[],'execution_step_limit':limit,'run_budget_cny':5,'model_call_limit':80,'repair_round':0,'repair_limit':3,'template_id':template_id,'template_digest':t['digest']}
            store.create_run(rid,state['user_request'],state,'offline')
            project.atomic_json(runner.ROOT/'outputs/evaluation-progress.json',{'phase':f'{template_id} · {limit}步策略','done':len(rows)})
            def fixed(run_id,stage,system,user,schema,**kw):
                ctx=json.loads(user)
                if stage.startswith('agent-planner-'):
                    return schema.model_validate({'action':'submit','summary':'固定模板核对计划','plan':{'steps':[{'title':'核对实现','description':'读取当前实现','depends_on':[],'acceptance_indices':list(range(len(ctx['acceptance_criteria'])))},{'title':'核对测试','description':'读取测试','depends_on':[1]},{'title':'记录版本','description':'保存快照','depends_on':[2]}]}}),{}
                turn=int(stage.rsplit('-',1)[1])
                if stage.startswith('agent-reviewer-'):
                    if turn<=2:return schema(action='tool',summary='独立读取模板证据',tool='project.read_file',arguments={'path':'src/App.tsx' if turn==1 else 'tests/e2e.spec.ts'}),{}
                    ok=all(ctx['technical'].get(x) for x in ('build_passed','test_passed','health_passed','environment_verified'))
                    verdict='blocked' if ctx.get('execution_incomplete') or not ok else 'ready_for_manual'
                    return schema(action='submit',summary='固定响应流程核对',review=team.ReviewReport(verdict=verdict,summary='合成数据，仅验证协作流程',criteria=[team.CriterionReview(index=i,status='manual',evidence='业务效果仍需真实任务和人工验收') for i in range(len(ctx['acceptance_criteria']))])),{}
                if turn in (1,3):return model.ExecutorDecision(action='tool',task_id=f'task-{(turn+1)//2}',tool='project.read_file',arguments={'path':'src/App.tsx' if turn==1 else 'tests/e2e.spec.ts'},summary='固定读取'),{}
                if turn==5:return model.ExecutorDecision(action='tool',task_id='task-3',tool='git.snapshot',arguments={},summary='固定快照'),{}
                if turn in (2,4,6):return model.ExecutorDecision(action='complete_task',task_id=f'task-{turn//2}',summary='固定任务完成'),{}
                return model.ExecutorDecision(action='finish',summary='固定执行结束'),{}
            def forbidden(*args,**kwargs):raise RuntimeError('Real providers are forbidden in offline evaluation')
            graph.generate_json=fixed;executor.generate_json=fixed;model._client=forbidden
            flow=graph.build_graph(runner.ROOT/'data/checkpoints.sqlite3')
            try:result=flow.invoke(state,{'configurable':{'thread_id':rid},'recursion_limit':256})
            finally:flow.checkpointer.conn.close()
            expected='completed' if limit==40 else 'failed_validation'
            rows.append({'case':template_id,'strategy':f'{"standard" if limit==40 else "bounded"}-{limit}','status':result['status'],'expected_status':expected,
                         'mechanism_passed':result['status']==expected and result.get('build_status')=='passed' and result.get('test_status')=='passed',
                         'build':result.get('build_status'),'test':result.get('test_status'),'review':result.get('review_status'),'steps':result.get('team_steps',0),
                         'repair_rounds':result.get('repair_round',0),'estimated_cost_cny':0,'provider_calls':0,'seconds':round(time.monotonic()-started,2),'environment':runtime_identity.require(rid)})
    project.atomic_json(runner.ROOT/'outputs/evaluation-result.json',{'suite':'local-collaboration-v1','mode':'deterministic_offline','provider_calls':0,'cases':rows,'passed':all(x['mechanism_passed'] for x in rows),'notice':'固定响应和内置源码，检查协作/预算交接/构建测试，不测量真实模型生成成功率。'})
if __name__=='__main__':main()
