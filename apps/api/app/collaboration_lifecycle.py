"""User-owned lifecycle operations. Frozen references block destructive removal."""
import hashlib,json,uuid,functools
from contextlib import nullcontext
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import collaboration as c,store,project
router=APIRouter(prefix='/api/v1/collaboration/projects')
KINDS={'note','baseline','comparison'}

def scope_for(rid,kind):
    c.require(rid)
    if kind not in KINDS:raise HTTPException(422,'不支持的资料类型。')
    return c.lineage(rid) if kind=='baseline' else str(rid)

def find(conn,scope,kind,identifier):
    c.setup(conn);row=conn.execute('SELECT value FROM collaboration_records WHERE scope=? AND kind=? AND id=?',(scope,kind,str(identifier))).fetchone()
    if not row:raise HTTPException(404,'资料不存在。')
    return json.loads(row[0])

def contains(value,identifier):
    if isinstance(value,dict):return any(contains(v,identifier) for v in value.values())
    if isinstance(value,list):return any(contains(v,identifier) for v in value)
    return value==identifier

def references(conn,scope,kind,identifier):
    out=[]
    for row in conn.execute('SELECT scope,kind,id,value FROM collaboration_records'):
        if tuple(row[:3])==(scope,kind,identifier):continue
        if contains(json.loads(row[3]),identifier):out.append({'type':row[1],'id':row[2],'scope':row[0]})
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('runs','product_designs'):
        if table not in tables:continue
        for row in conn.execute('SELECT id,state FROM '+table):
            if contains(json.loads(row[1]),identifier):out.append({'type':table,'id':row[0]})
    return out

def deletion_plan(conn,rid,kind,identifier,scope):
    v=find(conn,scope,kind,identifier);refs=references(conn,scope,kind,str(identifier))
    images=[v[k] for k in ('image','diff_image') if v.get(k)]
    value={'record':v,'references':refs,'images':images,'image_run_id':v.get('run_id',str(rid)),'can_delete':not refs}
    value['token']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest();return value

@router.get('/{rid}/records')
def search(rid:uuid.UUID,q:str='',archived:bool=False):
    if len(q)>200:raise HTTPException(422,'搜索词过长。')
    rows=[]
    for kind in KINDS:
        for v in c.records(scope_for(rid,kind),kind):
            if bool(v.get('archived'))!=archived:continue
            if q.casefold() not in ' '.join(str(v.get(k,'')) for k in ('comment','label','created_at','id','route')).casefold():continue
            rows.append({**v,'kind':kind})
    return {'items':sorted(rows,key=lambda x:x.get('created_at',''),reverse=True),'limits':{'note':40,'baseline':20,'comparison':20},'notice':'归档释放活动条数；已被任务、方案或比较引用的记录不可删除。'}

class ArchiveInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    archived:bool
    revision:int=Field(ge=1)

@router.post('/{rid}/records/{kind}/{identifier}/archive')
@c.serialized
def archive(rid:uuid.UUID,kind:str,identifier:uuid.UUID,payload:ArchiveInput):
    scope=scope_for(rid,kind)
    with store.db_connection() as conn:
        c.setup(conn);conn.execute('BEGIN IMMEDIATE');v=find(conn,scope,kind,identifier)
        if v.get('revision',1)!=payload.revision:raise HTTPException(409,'记录已变化，请刷新。')
        if not payload.archived:
            active=sum(not json.loads(row[0]).get('archived') for row in conn.execute('SELECT value FROM collaboration_records WHERE scope=? AND kind=?',(scope,kind)))
            if v.get('archived') and active>=({'note':40}.get(kind,20)):raise HTTPException(409,'活动记录已满，请先归档其他记录。')
        v.update(archived=payload.archived,revision=payload.revision+1,updated_at=c.now())
        conn.execute('UPDATE collaboration_records SET revision=?,value=? WHERE scope=? AND kind=? AND id=?',(v['revision'],json.dumps(v,ensure_ascii=False),scope,kind,str(identifier)))
    return v

@router.get('/{rid}/records/{kind}/{identifier}/deletion')
def preview_delete(rid:uuid.UUID,kind:str,identifier:uuid.UUID):
    scope=scope_for(rid,kind)
    with store.db_connection() as conn:return deletion_plan(conn,rid,kind,identifier,scope)

class DeleteInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:str=Field(pattern='^[0-9a-f]{64}$')
    confirmed:bool=False

@router.post('/{rid}/records/{kind}/{identifier}/delete')
@c.serialized
def delete(rid:uuid.UUID,kind:str,identifier:uuid.UUID,payload:DeleteInput):
    scope=scope_for(rid,kind)
    with store.db_connection() as conn:
        c.setup(conn);conn.execute('BEGIN IMMEDIATE');plan=deletion_plan(conn,rid,kind,identifier,scope)
        if not payload.confirmed or payload.token!=plan['token']:raise HTTPException(409,'请重新检查删除预览并明确确认。')
        if not plan['can_delete']:raise HTTPException(409,'历史任务或其他资料仍引用此记录，只能归档。')
        conn.execute('DELETE FROM collaboration_records WHERE scope=? AND kind=? AND id=?',(scope,kind,str(identifier)))
    from .page_feedback import folder
    import re
    for image in plan['images']:
        if re.fullmatch(r'[0-9a-f-]{36}(?:-diff)?',image):
            path=folder(plan['image_run_id'])/(image+'.png')
            if path.is_file() and not path.is_symlink():path.unlink()
    return {'deleted':True,'id':str(identifier)}

class FeedbackState(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    status:Literal['pending','adopted','implemented','awaiting_confirmation','closed']
    implementation_run_id:uuid.UUID|None=None
    explanation:str=Field(min_length=3,max_length=600)
    confirmed:bool=False

def lock_feedback(fn):
    @functools.wraps(fn)
    def wrapped(rid,identifier,payload):
        with project.project_lock(str(payload.implementation_run_id)) if payload.implementation_run_id else nullcontext():
            return fn(rid,identifier,payload)
    return wrapped

@router.post('/{rid}/notes/{identifier}/status')
@c.serialized
@lock_feedback
def feedback_status(rid:uuid.UUID,identifier:uuid.UUID,payload:FeedbackState):
    scope=scope_for(rid,'note');implementation=None
    if payload.status in {'implemented','awaiting_confirmation','closed'}:
        if not payload.implementation_run_id:raise HTTPException(422,'请选择落实意见的版本。')
        child=str(payload.implementation_run_id);state=c.require(child)
        if child==str(rid) or c.lineage(child)!=c.lineage(rid) or not any(x['id']==str(identifier) for x in state.get('selected_annotations',[])):raise HTTPException(409,'版本须属于本谱系且冻结了这条意见。')
        with project.project_lock(child):
            files=project.sources(child);digest=project.source_digest(files)
            from .tools import successful_validation
            checks={k:bool(successful_validation(child,'project.'+k,digest)) for k in ('build','test')}
            from . import acceptance
            test=successful_validation(child,'project.test',digest) or {}
            checks['test']=checks['test'] and test.get('suite_sha256')==acceptance.suite_hash(state.get('acceptance_profile','generated')) and test.get('health_sha256')==acceptance.health_hash(child)
            if payload.status in {'awaiting_confirmation','closed'} and not all(checks.values()):raise HTTPException(409,'等待确认需要所选版本当前源码的构建与测试证据。')
            implementation={'run_id':child,'source_digest':digest,'checks':checks}
        if payload.status=='closed' and not payload.confirmed:raise HTTPException(422,'关闭意见须由用户明确确认，不等于自动验收。')
    transitions={'pending':{'adopted'},'adopted':{'pending','implemented'},'implemented':{'adopted','awaiting_confirmation'},'awaiting_confirmation':{'implemented','closed'},'closed':{'pending'}}
    with store.db_connection() as conn:
        c.setup(conn);conn.execute('BEGIN IMMEDIATE');v=find(conn,scope,'note',identifier)
        if v.get('revision',1)!=payload.revision:raise HTTPException(409,'意见状态已变化，请刷新。')
        if payload.status not in transitions[v.get('status','pending')]:raise HTTPException(409,'请按采用、实施、等待确认、人工关闭的顺序推进；关闭后可重新打开。')
        history=v.get('status_history',[])
        if len(history)>=100:raise HTTPException(409,'状态历史已达到100条。')
        entry={'status':payload.status,'at':c.now(),'explanation':payload.explanation,'implementation':implementation,'actor':'user'}
        v.update(status=payload.status,revision=payload.revision+1,implementation=implementation,status_history=[*history,entry])
        conn.execute('UPDATE collaboration_records SET revision=?,value=? WHERE scope=? AND kind=? AND id=?',(v['revision'],json.dumps(v,ensure_ascii=False),scope,'note',str(identifier)))
    return v

@router.get('/{rid}/notes/{identifier}/versions')
def implementation_versions(rid:uuid.UUID,identifier:uuid.UUID):
    scope_for(rid,'note');items=[]
    with store.db_connection() as conn:
        find(conn,str(rid),'note',identifier)
        for row in conn.execute('SELECT id,state FROM runs'):
            state=json.loads(row[1])
            if any(v.get('id')==str(identifier) for v in state.get('selected_annotations',[])) and c.lineage(row[0])==c.lineage(rid):items.append({'run_id':row[0],'status':state.get('status'),'name':(state.get('product_spec') or {}).get('product_name','项目')})
    return {'items':items}

def adopt_in_transaction(conn,state):
    """The user's exact proposal confirmation adopts feedback atomically with its run."""
    parent=state.get('parent_run_id')
    if not parent:return
    c.setup(conn)
    for frozen in state.get('selected_annotations',[]):
        row=conn.execute("SELECT value FROM collaboration_records WHERE scope=? AND kind='note' AND id=?",(parent,frozen['id'])).fetchone()
        if not row:continue
        note=json.loads(row[0])
        if note.get('status','pending')!='pending':continue
        history=note.get('status_history',[])
        if len(history)>=100:continue
        note.update(status='adopted',revision=note.get('revision',1)+1,status_history=[*history,{'status':'adopted','at':c.now(),'actor':'user_confirmation','explanation':'确认方案并创建实施任务','implementation':{'run_id':state['run_id']}}])
        conn.execute("UPDATE collaboration_records SET revision=?,value=? WHERE scope=? AND kind='note' AND id=?",(note['revision'],json.dumps(note,ensure_ascii=False),parent,frozen['id']))
