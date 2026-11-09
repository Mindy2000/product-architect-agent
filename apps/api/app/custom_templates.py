"""Explicitly reviewed, immutable reusable source bundles. No data/config export."""
import hashlib,json,re,uuid
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import collaboration as c,project,designs,store
from .security_policy import redact
router=APIRouter(prefix='/api/v1/custom-templates')

def clean(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if re.fullmatch(r'(?i)(api[_-]?key|password|secret|access[_-]?token)',str(key)) and isinstance(item,str) and len(item)>=4:return False
            if not clean(str(key)) or not clean(item):return False
        return True
    if isinstance(value,list):return all(clean(item) for item in value)
    if not isinstance(value,str):return True
    if redact(value)!=value:return False
    # Inspect actual strings, before JSON escaping can hide quotes in source code.
    return not re.search(r"(?i)(?:api[_-]?key|password|secret|access[_-]?token)\s*[\"']?\s*[:=]\s*[\"'][^\"']{4,}|-----BEGIN .*PRIVATE KEY|/Users/|[A-Z]:\\+Users\\+|[\w.+-]+@[\w.-]+\.[a-z]{2,}",value)

@router.get('/projects/{rid}/candidate')
def candidate(rid:uuid.UUID):
    state=c.require(rid)
    if state.get('status') not in {'completed','paused','failed_validation','failed','interrupted','cancelled'}:raise HTTPException(409,'请先暂停任务或等待完成。')
    files=project.sources(str(rid));spec=state.get('product_spec') or {}
    try:designs.ProductDesign.model_validate(spec)
    except ValueError:raise HTTPException(409,'此历史项目缺少完整页面方案，请先补全产品方案。')
    values=[]
    for path,text in files.items():
        if not project.managed_path(path):continue
        values.append({'path':path,'content':redact(text) if clean(text) else '', 'blocked':not clean(text),'bytes':len(text.encode())})
    return {'source_digest':project.source_digest(files),'files':values,'spec':spec if clean(spec) else None,
            'rules':[x for x in c.records(c.lineage(rid),'rule') if x['active'] and c.applicable(x,{}) and clean(x)],
            'notice':'仅所选受控源码、测试和手选通用规则。不会包含数据库、附件、浏览器数据、配置或密钥。自动检测无法识别所有业务隐私，请逐项检查和编辑。'}

class TemplateInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_run_id:uuid.UUID
    source_digest:str=Field(pattern='^[0-9a-f]{64}$')
    name:str=Field(min_length=1,max_length=100)
    previous_id:uuid.UUID|None=None
    files:dict[str,str]=Field(max_length=128)
    spec:designs.ProductDesign
    rule_ids:list[uuid.UUID]=Field(default_factory=list,max_length=20)

def prepare(payload):
    state=c.require(payload.source_run_id)
    if state.get('status') not in {'completed','paused','failed_validation','failed','interrupted','cancelled'}:raise HTTPException(409,'请先停止任务。')
    rid=str(payload.source_run_id);files=project.sources(rid)
    if project.source_digest(files)!=payload.source_digest:raise HTTPException(409,'源项目已变化，请重新预览。')
    if not payload.files.keys()<=files.keys():raise HTTPException(422,'只能选取源项目已有受控文件。')
    project.validate_sources(payload.files)
    if not any(p.startswith('tests/') for p in payload.files):raise HTTPException(422,'模板必须包含至少一份交互测试。')
    app_type=state.get('product_spec',{}).get('application_type','frontend')
    if payload.spec.application_type!=app_type:raise HTTPException(422,'模板应用类型必须与原项目一致。')
    if app_type=='fullstack_crud':
        expected=payload.spec.crud_schema.model_dump(mode='json')
        if json.loads(payload.files.get('backend/schema.json','{}'))!=expected:raise HTTPException(422,'模板后端数据结构与方案不一致。')
    rules=c.freeze_rules(rid,payload.rule_ids)
    value={'name':payload.name,'files':payload.files,'spec':payload.spec.model_dump(mode='json'),'rules':rules}
    if not clean(value):raise HTTPException(422,'所选内容包含疑似凭证、个人路径或邮箱，请编辑通用内容后再预览。')
    previous=None
    if payload.previous_id:
        previous=next((x for x in c.records('workbench','template') if x['id']==str(payload.previous_id)),None)
        if not previous:raise HTTPException(404,'旧模板版本不存在。')
        if any(x.get('group_id')==previous['group_id'] and x['version']>previous['version'] for x in c.records('workbench','template')):raise HTTPException(409,'请选择模板最新版本。')
    value.update(version=previous['version']+1 if previous else 1,previous_id=str(payload.previous_id) if previous else None,group_id=previous['group_id'] if previous else None,
                 source_run_id=rid,source_digest=payload.source_digest)
    value['digest']=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return value

@router.post('/preview')
@c.serialized
def preview(payload:TemplateInput):return {'bundle':prepare(payload),'excluded':['.env','密钥配置','业务数据库与附件','个人浏览器存储','历史任务与检索资料'],'requires_confirmation':True}

class SaveTemplate(TemplateInput):
    token:str=Field(pattern='^[0-9a-f]{64}$')
    confirmed:bool=False

@router.post('')
@c.serialized
def save(payload:SaveTemplate):
    with project.project_lock(str(payload.source_run_id)):
        value=prepare(payload)
        if not payload.confirmed or payload.token!=value['digest']:raise HTTPException(409,'请重新预览并确认模板内容。')
        value.update(id=str(uuid.uuid4()),created_at=c.now(),custom=True)
        if not value['group_id']:value['group_id']=value['id']
        return c.insert('workbench','template',value,60)

def load(identifier):
    try:uid=str(uuid.UUID(identifier.removeprefix('custom-')))
    except ValueError:raise HTTPException(404,'模板不存在。')
    value=next((v for v in c.records('workbench','template') if v['id']==uid),None)
    if not value:raise HTTPException(404,'模板不存在。')
    return {**value,'id':'custom-'+value['id']}
