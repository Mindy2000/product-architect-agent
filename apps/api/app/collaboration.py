"""User-owned project rules, bounded clarification, and frozen collaboration context."""
import hashlib,json,uuid
from datetime import datetime,timezone
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import store,project,runner
router=APIRouter(prefix='/api/v1/collaboration')

def now():return datetime.now(timezone.utc).isoformat()
def require(identifier):
    value=store.get_run(str(identifier))
    if not value:raise HTTPException(404,'项目不存在。')
    return value

def lineage(identifier):
    seen=set();current=str(identifier)
    for _ in range(256):
        if current in seen:raise HTTPException(409,'项目谱系存在循环。')
        seen.add(current);value=require(current)
        if not value.get('parent_run_id'):return current
        current=value['parent_run_id']
    raise HTTPException(409,'项目谱系超过可处理范围。')

def setup(c):
    c.execute('CREATE TABLE IF NOT EXISTS collaboration_records (scope TEXT, kind TEXT, id TEXT, revision INTEGER, value TEXT, PRIMARY KEY(scope,kind,id))')

def records(scope,kind):
    with store.db_connection() as c:
        setup(c);return [json.loads(r[0]) for r in c.execute('SELECT value FROM collaboration_records WHERE scope=? AND kind=? ORDER BY rowid',(scope,kind))]

def insert(scope,kind,value,limit=40):
    with store.db_connection() as c:
        setup(c);c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT COUNT(*) FROM collaboration_records WHERE scope=? AND kind=?',(scope,kind)).fetchone()[0]>=limit:raise HTTPException(409,'该项目记录已达到上限，请复用已有记录。')
        c.execute('INSERT INTO collaboration_records VALUES (?,?,?,?,?)',(scope,kind,value['id'],value.get('revision',1),json.dumps(value,ensure_ascii=False)))
    return value

class RuleInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=100)
    content:str=Field(min_length=1,max_length=1200)
    source:str=Field(default='用户确认',min_length=1,max_length=200)
    active:bool=True
    revision:int=Field(default=0,ge=0)

@router.get('/projects/{identifier}/rules')
def rules(identifier:uuid.UUID):return {'scope':lineage(identifier),'items':records(lineage(identifier),'rule')}

@router.post('/projects/{identifier}/rules')
def add_rule(identifier:uuid.UUID,payload:RuleInput):
    require(identifier);scope=lineage(identifier);v={**payload.model_dump(),'id':str(uuid.uuid4()),'revision':1,'created_from':str(identifier),'history':[],'updated_at':now()}
    v['history']=[{k:v[k] for k in ('revision','content','title','active','source','updated_at')}]
    return insert(scope,'rule',v,20)

@router.put('/projects/{identifier}/rules/{rule_id}')
def edit_rule(identifier:uuid.UUID,rule_id:uuid.UUID,payload:RuleInput):
    scope=lineage(identifier)
    with store.db_connection() as c:
        setup(c);c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM collaboration_records WHERE scope=? AND kind=? AND id=?',(scope,'rule',str(rule_id))).fetchone()
        if not row:raise HTTPException(404,'规则不存在。')
        v=json.loads(row[0])
        if payload.revision!=v['revision']:raise HTTPException(409,'规则已修改，请刷新后再保存。')
        if len(v['history'])>=100:raise HTTPException(409,'规则修订已达到100条上限。')
        v.update(**{**payload.model_dump(),'revision':v['revision']+1,'updated_at':now()})
        v['history'].append({k:v[k] for k in ('revision','content','title','active','source','updated_at')})
        c.execute('UPDATE collaboration_records SET revision=?,value=? WHERE scope=? AND kind=? AND id=?',(v['revision'],json.dumps(v,ensure_ascii=False),scope,'rule',str(rule_id)))
    return v

def freeze_rules(parent):
    if not parent:return []
    return [{k:v[k] for k in ('id','revision','title','content','source','created_from')} for v in records(lineage(parent),'rule') if v['active']]

class QuestionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:str=Field(min_length=8,max_length=600)
    reason:str=Field(min_length=5,max_length=600)
    options:list[str]=Field(min_length=2,max_length=3)
    @classmethod
    def checked(cls,value):
        if any(not x.strip() or len(x)>200 for x in value.options) or len(set(value.options))!=len(value.options):raise project.ProjectError('澄清需提供2–3个不同的简短选项。')
        return value

def ask(context,args):
    QuestionInput.checked(args);state=require(context.run_id)
    if state.get('collaboration_version')!=1:raise project.ProjectError('旧任务保持原协议，不支持新增澄清。')
    identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,context.run_id+':'+context.operation_id))
    existing=records(context.run_id,'question')
    for q in existing:
        if q['id']==identifier:return q
    if any(q['status']=='pending' for q in existing):raise project.ProjectError('请先等待当前问题的回答。')
    value={**args.model_dump(),'id':identifier,'status':'pending','agent':context.agent_id,'source_digest':project.manifest(context.run_id)['digest'],'created_at':now(),'answer':None}
    try:return insert(context.run_id,'question',value,3)
    except HTTPException as e:raise project.ProjectError(str(e.detail)) from e

def pending(identifier):return next((x for x in records(identifier,'question') if x['status']=='pending'),None)

class Answer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    answer:str=Field(min_length=1,max_length=1200)

@router.post('/runs/{identifier}/questions/{question_id}/answer')
def answer(identifier:uuid.UUID,question_id:uuid.UUID,payload:Answer):
    state=require(identifier)
    if state.get('status') not in {'paused','interrupted'}:raise HTTPException(409,'请等待任务进入澄清暂停状态。')
    with project.project_lock(str(identifier)),store.db_connection() as c:
        project.sources(str(identifier))
        setup(c);c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM collaboration_records WHERE scope=? AND kind=? AND id=?',(str(identifier),'question',str(question_id))).fetchone()
        if not row:raise HTTPException(404,'问题不存在。')
        q=json.loads(row[0])
        if q['status']!='pending':raise HTTPException(409,'问题已回答，不重复提交。')
        if project.manifest(str(identifier))['digest']!=q['source_digest']:raise HTTPException(409,'提问后源码已变化，请保留记录并另建修改任务。')
        q.update(status='answered',answer=payload.answer,answered_at=now())
        c.execute('UPDATE collaboration_records SET value=? WHERE scope=? AND kind=? AND id=?',(json.dumps(q,ensure_ascii=False),str(identifier),'question',str(question_id)))
    return q

@router.get('/runs/{identifier}')
def run_context(identifier:uuid.UUID):
    state=require(identifier)
    return {'questions':records(str(identifier),'question'),'frozen_rules':state.get('project_rules',[]),'annotations':state.get('selected_annotations',[]),'template':state.get('template_id')}

def context(state):
    return {'project_rules':state.get('project_rules',[]),'page_feedback':state.get('selected_annotations',[]),
            'clarifications':records(state['run_id'],'question') if state.get('collaboration_version')==1 else [],
            'instruction':'规则和回答为用户确认的项目上下文；不改变冻结规格、工具权限或预算。范围冲突应停止并说明，不擅自扩展。'}
