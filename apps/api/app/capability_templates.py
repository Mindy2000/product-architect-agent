"""Reviewed local source bundles; choosing a template never invokes a model."""
from typing import Literal
import hashlib,json,uuid
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import runner,designs,collaboration as c,project
router=APIRouter(prefix='/api/v1/templates')
IDS=('tasks','form','dashboard')

def load(identifier):
    if identifier.startswith('custom-'):
        from .custom_templates import load as custom_load
        return custom_load(identifier)
    if identifier not in IDS:raise HTTPException(404,'能力模板不存在。')
    path=runner.ROOT/'templates/capabilities'/(identifier+'.json')
    if path.is_symlink():raise runner.SandboxError('模板路径异常。')
    raw=path.read_bytes();value=json.loads(raw);designs.ProductDesign.model_validate(value['spec'])
    value['digest']=hashlib.sha256(raw).hexdigest();return value

@router.get('')
def catalog():
    ids=[*IDS,*['custom-'+v['id'] for v in c.records('workbench','template')]]
    return [{k:v for k,v in load(i).items() if k not in {'files','rules'}} for i in ids]

class DraftInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    model_name:designs.TextModel='deepseek-flash'
    role_models:dict[Literal['planner','developer','reviewer'],designs.TextModel]=Field(default_factory=dict,max_length=3)
    run_budget_cny:float=Field(default=5,ge=.25,le=20)
    execution_step_limit:int=Field(default=40,ge=8,le=64)

@router.post('/{identifier}/draft')
def draft(identifier:str,payload:DraftInput):
    template=load(identifier);now=c.now();rid=str(uuid.uuid4())
    settings=designs.DesignRequest(application_type=template['spec'].get('application_type','frontend'),request='以本地能力模板开始：'+template['spec']['product_name'],**payload.model_dump()).model_dump(mode='json')
    value={**settings,'design_id':rid,'revision':1,'status':'ready','spec':template['spec'],'template_id':identifier,'template_digest':template['digest'],
           'reference_snapshot':[],'knowledge_context':None,'web_snapshot':[],'project_rules':template.get('rules',[]),'selected_annotations':[],
           'error':'','created_at':now,'updated_at':now,'history':[{'action':'template','at':now,'revision':1}]}
    conn=designs.connection()
    try:
        conn.execute('INSERT INTO product_designs VALUES (?,?,?)',(rid,json.dumps(value,ensure_ascii=False),now));conn.commit()
    finally:conn.close()
    return designs.public(value)

def seed(state):
    if not state.get('template_id'):return
    if state.get('parent_run_id'):raise runner.SandboxError('能力模板不能覆盖已有项目。')
    template=load(state['template_id'])
    if template['digest']!=state.get('template_digest'):raise runner.SandboxError('模板版本已变化，请重新选择并确认方案。')
    journal=project.metadata_dir(state['run_id'])/'capability-template.json'
    if journal.exists():
        if json.loads(journal.read_text())['digest']!=template['digest']:raise runner.SandboxError('模板初始化记录不匹配。')
        return
    before=project.sources(state['run_id']);changes=[]
    for path,content in template['files'].items():
        if before.get(path)==content:continue
        changes.append(project.FileChange(operation='replace' if path in before else 'create',path=path,content=content,expected_sha256=project.digest(before[path]) if path in before else None))
    if changes:project.apply_changes(state['run_id'],changes,'capability_template_v1')
    project.atomic_json(journal,{'digest':template['digest']})
