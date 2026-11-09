"""Deterministic failure injection in the evaluator's disposable project only."""
import json,time,uuid
from . import collaboration as c,tools,store,project,team_policy

def run(rid):
    rows=[]
    def check(name,fn):
        started=time.monotonic()
        try:fn();passed=True
        except Exception:passed=False
        rows.append({'case':name,'strategy':'fault-injection','status':'expected_guard' if passed else 'unexpected','mechanism_passed':passed,'steps':0,'repair_rounds':0,'seconds':round(time.monotonic()-started,3),'estimated_cost_cny':0,'provider_calls':0})
    def bad_arguments():
        r=tools.execute(tools.ToolContext(rid,'fixture','fault-args',agent_id='developer'),'project.read_file',{'path':['wrong-type']})
        assert not r['ok'] and r['error']['code']=='invalid_arguments'
    check('malformed_arguments',bad_arguments)
    def stale():
        conn=tools.connect()
        try:row=conn.execute("SELECT * FROM tool_calls WHERE run_id=? AND tool_name='project.build' AND status='completed' ORDER BY completed_at DESC LIMIT 1",(rid,)).fetchone()
        finally:conn.close()
        assert row
        old=project.sources(rid)['src/App.tsx'];project.apply_changes(rid,[project.FileChange(operation='replace',path='src/App.tsx',content=old+'\n// stale-evidence fixture\n',expected_sha256=project.digest(old))],'fault-stale')
        agent=json.loads(row['result_json']).get('agent_id','system')
        r=tools.execute(tools.ToolContext(rid,row['task_id'],row['operation_id'],agent_id=agent),'project.build',json.loads(row['arguments_json']))
        assert r['error']['code']=='stale_validation'
    check('stale_evidence',stale)
    def clarify():
        state=store.get_run(rid);store.update_run(rid,{**state,'status':'paused'})
        q=c.ask(tools.ToolContext(rid,'fixture','fault-question',agent_id='planner'),c.QuestionInput(question='合成任务需要使用哪种排序？',reason='验证暂停后问题持久化',options=['创建时间','标题']))
        assert c.pending(rid)['id']==q['id']
        c.answer(uuid.UUID(rid),uuid.UUID(q['id']),c.Answer(answer='创建时间'));assert c.pending(rid) is None
        assert c.records(rid,'question')[-1]['status']=='answered'
    check('clarification_interrupt',clarify)
    def conflict():
        for value in ('blue','red'):c.add_rule(uuid.UUID(rid),c.RuleInput(title='合成配色',content='明确配色'+value,constraints={'primary_color':value}))
        from fastapi import HTTPException
        try:c.freeze_rules(rid)
        except HTTPException as e:assert e.status_code==409;return
        raise AssertionError('conflict allowed')
    check('rule_conflict',conflict)
    def no_progress():
        basis={'source_digest':project.manifest(rid)['digest'],'environment':{'digest':'fixture'}};report={'verdict':'changes_requested','criteria':[{'index':0,'status':'gap','evidence':'合成缺口'}]}
        first=team_policy.reconcile({'repair_round':0},report,basis,{})
        next_state=team_policy.reconcile({**first,'repair_round':1},report,basis,{})
        assert next_state['review_stop_reason'] and next_state['review_issues'][0]['status']=='open'
    check('no_progress_repair',no_progress)
    return rows
