"""User-owned project rules, bounded clarification, and frozen collaboration context."""
import hashlib,json,uuid,threading,functools
from typing import Literal
from pydantic import model_validator
from datetime import datetime,timezone
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import store,project,runner
router=APIRouter(prefix='/api/v1/collaboration')

_mutation_lock=threading.RLock()
def serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args,**kwargs):
        with _mutation_lock:return fn(*args,**kwargs)
    return wrapper

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
        rows=[json.loads(r[0]) for r in c.execute('SELECT value FROM collaboration_records WHERE scope=? AND kind=?',(scope,kind))]
        if sum(not r.get('archived') for r in rows)>=limit or len(rows)>=limit*5:raise HTTPException(409,'记录已达到活动或归档总上限，请归档或检查引用后删除无用记录。')
        c.execute('INSERT INTO collaboration_records VALUES (?,?,?,?,?)',(scope,kind,value['id'],value.get('revision',1),json.dumps(value,ensure_ascii=False)))
    return value

class RuleInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=100)
    content:str=Field(min_length=1,max_length=1200)
    source:str=Field(default='用户确认',min_length=1,max_length=200)
    scope_type:Literal['project','page','feature','task']='project'
    targets:list[str]=Field(default_factory=list,max_length=12)
    constraints:dict[str,str]=Field(default_factory=dict,max_length=12)
    @model_validator(mode='after')
    def bounded_scope(self):
        if any(not x.strip() or len(x)>120 for x in self.targets):raise ValueError('范围标记须为1–120字符')
        if self.scope_type!='project' and not self.targets:raise ValueError('请填写适用页面、功能或任务标记')
        if any(not k.strip() or len(k)>80 or not v.strip() or len(v)>200 for k,v in self.constraints.items()):raise ValueError('结构化规则键值过长或为空')
        return self
    active:bool=True
    revision:int=Field(default=0,ge=0)

@router.get('/projects/{identifier}/rules')
def rules(identifier:uuid.UUID):return {'scope':lineage(identifier),'items':records(lineage(identifier),'rule')}

@router.post('/projects/{identifier}/rules')
def add_rule(identifier:uuid.UUID,payload:RuleInput):
    require(identifier);scope=lineage(identifier);v={**payload.model_dump(),'id':str(uuid.uuid4()),'revision':1,'created_from':str(identifier),'history':[],'updated_at':now()}
    v['history']=[{k:v[k] for k in ('revision','content','title','active','source','updated_at','scope_type','targets','constraints')}]
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
        v['history'].append({k:v[k] for k in ('revision','content','title','active','source','updated_at','scope_type','targets','constraints')})
        c.execute('UPDATE collaboration_records SET revision=?,value=? WHERE scope=? AND kind=? AND id=?',(v['revision'],json.dumps(v,ensure_ascii=False),scope,'rule',str(rule_id)))
    return v

def applicable(rule,scope):
    kind=rule.get('scope_type','project')
    return kind=='project' or bool(set(rule.get('targets',[]))&set(scope.get(kind,[])))

def conflicts(rules):
    warnings=[]
    for i,a in enumerate(rules):
        for b in rules[i+1:]:
            if a.get('content','').strip()==b.get('content','').strip():warnings.append({'kind':'duplicate','ids':[a['id'],b['id']],'message':'规则正文重复'})
            for key,value in a.get('constraints',{}).items():
                if key in b.get('constraints',{}) and b['constraints'][key]!=value:warnings.append({'kind':'conflict','ids':[a['id'],b['id']],'key':key,'message':'相同结构化字段取值不同'})
    return warnings

def freeze_rules(parent,ids=None,scope=None):
    if not parent:return []
    values=[v for v in records(lineage(parent),'rule') if v['active'] and applicable(v,scope or {})]
    if ids is not None:
        wanted={str(x) for x in ids}
        if not wanted<={v['id'] for v in values}:raise HTTPException(409,'选中的规则已撤销、范围不匹配或不属于项目。')
        values=[v for v in values if v['id'] in wanted]
    if any(w['kind']=='conflict' for w in conflicts(values)):raise HTTPException(409,'适用规则有结构化字段冲突，请修订或取消本次选择。')
    return [{k:v[k] for k in ('id','revision','title','content','source','created_from','scope_type','targets','constraints') if k in v} for v in values]

class RuleSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rule_ids:list[uuid.UUID]|None=Field(default=None,max_length=20)
    page:list[str]=Field(default_factory=list,max_length=12)
    feature:list[str]=Field(default_factory=list,max_length=12)
    task:list[str]=Field(default_factory=list,max_length=12)
    @model_validator(mode='after')
    def bounded(self):
        if any(not x.strip() or len(x)>120 for key in ('page','feature','task') for x in getattr(self,key)):raise ValueError('适用范围标记须为1–120字符')
        return self

@router.post('/projects/{identifier}/rules/selection')
def rule_selection(identifier:uuid.UUID,payload:RuleSelection):
    scope=payload.model_dump();items=[v for v in records(lineage(identifier),'rule') if v['active'] and applicable(v,scope)]
    chosen=items if payload.rule_ids is None else [v for v in items if v['id'] in {str(x) for x in payload.rule_ids}]
    return {'items':items,'warnings':conflicts(chosen),'notice':'只检查明确结构化键值及重复文本；自由文本含义冲突仍需人工核对。'}

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
            'source_mapping_contract':'需要页面点选定位时，可为组件根元素添加 data-pa-source=源码路径:实际行号。标记须随源码修改同步；无法保证位置时不伪造。',
            'instruction':'规则和回答为用户确认的项目上下文；不改变冻结规格、工具权限或预算。范围冲突应停止并说明，不擅自扩展。'}
