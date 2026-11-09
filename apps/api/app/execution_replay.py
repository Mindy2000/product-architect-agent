"""Read-only timeline and bounded fixed-decision source branches; never a provider call."""
import difflib,hashlib,json,tempfile,uuid
from pathlib import Path
from contextlib import closing
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import collaboration as c,project,runner,store,tools,team
from .security_policy import redact
router=APIRouter(prefix='/api/v1/replay')

def hashed(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

def snapshot(rid,point='current'):
    current=project.sources(rid)
    if point=='current':return current,{'kind':'current','digest':project.source_digest(current)}
    if ':' not in point:raise HTTPException(422,'无效回放位置。')
    name,side=point.rsplit(':',1)
    import re
    if side not in {'before','after'} or not re.fullmatch(r'edit-[A-Za-z0-9_-]{1,100}',name):raise HTTPException(422,'无效源码检查点。')
    path=project.metadata_dir(rid)/(name+'.json')
    if not path.is_file() or path.is_symlink() or path.stat().st_size>8*1024**2:raise HTTPException(409,'源码检查点缺失或过大。')
    journal=json.loads(path.read_text());files=journal[side];project.validate_sources(files)
    if not journal.get('result'):raise HTTPException(409,'未提交的源码操作不参与分支。')
    return files,{'kind':'source_journal','operation_id':name[5:],'side':side,'revision':journal['revision']-(side=='before'),'commit':journal['result'].get('commit'),'digest':project.source_digest(files)}

def collect(rid):
    state=c.require(rid);events=[]
    with closing(team.connect()) as conn:
        calls=conn.execute('SELECT step_id,role,status,request_json,value_json,usage_json,created_at FROM agent_calls WHERE run_id=? ORDER BY created_at LIMIT 201',(rid,)).fetchall()
        for row in calls[:200]:
            request=json.loads(row['request_json']);events.append({'kind':'agent','id':row['step_id'],'at':row['created_at'],'role':row['role'],'status':row['status'],'input':request,'output':json.loads(row['value_json']) if row['value_json'] and row['status']=='ready' else row['value_json'],'usage':json.loads(row['usage_json'] or '{}')})
        receipts=conn.execute('SELECT * FROM tool_calls WHERE run_id=? ORDER BY started_at LIMIT 501',(rid,)).fetchall()
        for row in receipts[:500]:events.append({'kind':'tool','id':row['operation_id'],'at':row['started_at'],'tool':row['tool_name'],'status':row['status'],'input':json.loads(row['arguments_json']),'output':json.loads(row['result_json'] or '{}')})
    points=[{'id':'current','label':'当前源码'}]
    for path in sorted(project.metadata_dir(rid).glob('edit-*.json'))[:256]:
        if path.is_symlink() or path.stat().st_size>8*1024**2:continue
        journal=json.loads(path.read_text())
        if not journal.get('result'):continue
        for side in ('before','after'):points.append({'id':path.stem+':'+side,'label':f"修订 {journal['revision']} · {'修改前' if side=='before' else '修改后'}",'source_digest':project.source_digest(journal[side])})
        events.append({'kind':'change','id':path.stem,'at':next((e.get('at','') for e in events if e['kind']=='tool' and e['id']==path.stem[5:]),state.get('created_at','')),'revision':journal['revision'],'paths':journal['result'].get('changed_paths',[]),'before':project.source_digest(journal['before']),'after':project.source_digest(journal['after']),'commit':journal['result'].get('commit'),'diff':'\n'.join(''.join(difflib.unified_diff(journal['before'].get(p,'').splitlines(True),journal['after'].get(p,'').splitlines(True),fromfile=p,tofile=p)) for p in journal['result'].get('changed_paths',[]))[:30000]})
    for i,e in enumerate(state.get('trace',[])[:500]):events.append({'kind':'trace','id':str(i),'detail':e,'at':e.get('at',e.get('timestamp','')) if isinstance(e,dict) else ''})
    events.sort(key=lambda x:(x.get('at',''),x.get('revision',0)))
    return state,events,points

def graph_checkpoints(rid):
    path=runner.ROOT/'data/checkpoints.sqlite3'
    if not path.is_file() or path.is_symlink():return []
    from .graph import build_graph
    flow=build_graph(path)
    try:
        return [{'checkpoint_id':v.config['configurable'].get('checkpoint_id'),'next':list(v.next),'at':v.created_at,
                 'source_digest':(v.values.get('project_manifest') or {}).get('digest'),
                 'steps':v.values.get('team_steps',v.values.get('executor_turns',0)),
                 'state_digest':hashed(v.values)} for v in flow.get_state_history({'configurable':{'thread_id':rid}},limit=80)]
    finally:flow.checkpointer.conn.close()

def binding(rid,point):
    state,events,points=collect(rid);files,checkpoint=snapshot(rid,point)
    checkpoints=graph_checkpoints(rid);matches=[v for v in checkpoints if v['source_digest']==checkpoint['digest']]
    from .model import budget_status
    basis={'run_id':rid,'source_checkpoint':checkpoint,'run_status':state.get('status'),'events_digest':hashed(events),
           'graph_checkpoints':matches,'checkpoint_match':bool(matches),'usage':budget_status(rid),'source_now':project.manifest(rid)['digest'],'steps':(matches[0]['steps'] if matches else state.get('team_steps',state.get('executor_turns',0))),
           'step_limit':state.get('execution_step_limit',40),'budget_cny':state.get('run_budget_cny',5),'repair_round':state.get('repair_round',0),
           'policy_digest':hashed((Path(__file__).read_text()))}
    return files,basis,hashed(basis)

@router.get('/{rid}')
def timeline(rid:uuid.UUID):
    state,events,points=collect(str(rid))
    return json.loads(redact(json.dumps({'events':events,'points':points,'status':state.get('status'),'stop_reason':state.get('error') or state.get('team_stop_reason') or state.get('execution_incomplete'),
        'checkpoints':graph_checkpoints(str(rid)),'branches':c.records(str(rid),'replay'),'notice':'按真实回执查看。旧任务缺少记录时不补造；源码检查点不是完整运行时快照。固定决策分支只核对源码与资源规则，不恢复真实模型或数据库副作用。'},ensure_ascii=False)))

@router.get('/{rid}/binding')
def preview_binding(rid:uuid.UUID,point:str='current'):
    with project.project_lock(str(rid)):
        files,basis,token=binding(str(rid),point)
        return {'basis':basis,'token':token,'files':[{'path':p,'sha256':project.digest(t)} for p,t in files.items()]}

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['read_file','apply_changes','finish','stop']
    path:str|None=Field(default=None,max_length=180)
    changes:list[project.FileChange]=Field(default_factory=list,max_length=16)
class Branch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=80)
    decisions:list[Decision]=Field(min_length=1,max_length=32)
class ReplayInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    point:str=Field(default='current',max_length=140)
    token:str=Field(pattern='^[0-9a-f]{64}$')
    branches:list[Branch]=Field(min_length=2,max_length=2)
    step_limit:int=Field(default=8,ge=1,le=32)


def apply_fixed(files,decision):
    after=dict(files)
    if decision.action=='read_file':
        if decision.path not in files:raise ValueError('读取路径不在所选源码检查点内')
        return after,{'path':decision.path,'sha256':project.digest(files[decision.path]),'content':files[decision.path][:12000]}
    if decision.action!='apply_changes':return after,{}
    if not decision.changes:raise ValueError('没有文件变更')
    seen=set()
    for change in decision.changes:
        path=change.path
        if path in seen or not project.managed_path(path):raise ValueError('重复路径或路径不受控')
        seen.add(path)
        if change.operation=='create':
            if path in after or change.expected_sha256 is not None or change.content is None or change.edits:raise ValueError('新建前置条件不符')
            after[path]=change.content
        else:
            if path not in after or project.digest(after[path])!=change.expected_sha256:raise ValueError('文件SHA256与检查点不符')
            if change.operation=='delete':
                if change.content is not None or change.edits:raise ValueError('删除不能带内容')
                after.pop(path)
            elif change.operation=='replace':
                if change.content is None or change.edits:raise ValueError('替换内容不完整')
                after[path]=change.content
            else:
                if not change.edits or change.content is not None:raise ValueError('补丁不完整')
                for edit in change.edits:
                    if after[path].count(edit.before)!=1:raise ValueError('补丁定位不唯一')
                    after[path]=after[path].replace(edit.before,edit.after,1)
    project.validate_sources(after);return after,{'changed_paths':sorted(p for p in files.keys()|after.keys() if files.get(p)!=after.get(p))}

@router.post('/{rid}/branches')
@c.serialized
def compare(rid:uuid.UUID,payload:ReplayInput):
    identifier=str(rid)
    with project.project_lock(identifier):
        state=c.require(identifier)
        if state.get('status') not in {'paused','completed','failed','failed_validation','cancelled','interrupted'}:raise HTTPException(409,'请先暂停原任务。')
        source,basis,token=binding(identifier,payload.point)
        if token!=payload.token:raise HTTPException(409,'源码、回执或运行状态已变化，请重新绑定回放位置。')
        if payload.step_limit>basis['step_limit']:raise HTTPException(422,'分支步骤不能超过原任务总限额。')
        results=[]
        with tempfile.TemporaryDirectory(prefix='replay-',dir=project.metadata_dir(identifier)) as tmp:
            for index,branch in enumerate(payload.branches):
                files=dict(source);events=[];reason='固定决策耗尽';folder=Path(tmp)/str(index);folder.mkdir()
                for i,d in enumerate(branch.decisions):
                    if i>=min(payload.step_limit,max(0,basis['step_limit']-basis['steps'])):reason='达到分支步骤上限';break
                    if d.action in {'finish','stop'}:reason='固定决策结束' if d.action=='finish' else '固定决策停止';events.append({'step':i+1,'action':d.action});break
                    try:files,result=apply_fixed(files,d)
                    except (ValueError,project.ProjectError) as e:reason='受控操作被拒绝';events.append({'step':i+1,'action':d.action,'error':str(e)});break
                    events.append({'step':i+1,'action':d.action,'result':result,'digest':project.source_digest(files)})
                # This directory contains text only, with no original keys, data, packages or executable commands.
                for path,text in files.items():
                    dest=folder/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(text)
                results.append({'label':branch.label,'steps':len(events),'stop_reason':reason,'events':events,'source_digest':project.source_digest(files),
                    'changed_files':sorted(p for p in source.keys()|files.keys() if source.get(p)!=files.get(p)),
                    'diff':'\n'.join(''.join(difflib.unified_diff(source.get(p,'').splitlines(True),files.get(p,'').splitlines(True),fromfile=p,tofile=p)) for p in sorted(source.keys()|files.keys()) if source.get(p)!=files.get(p))[:40000]})
        record={'id':str(uuid.uuid4()),'created_at':c.now(),'basis':basis,'token':token,'branches':results,'decisions':[b.model_dump(mode='json') for b in payload.branches],
                'provider_calls':0,'cost_cny':0,'same_source':results[0]['source_digest']==results[1]['source_digest'],'mode':'fixed_source_decisions','notice':'独立文本副本；未执行生成代码、模型、构建或原工具副作用。原任务预算与检查点保持不变；运行质量须另行验证。'}
        return c.insert(identifier,'replay',record,20)
