"""Local preview evidence, user-selected feedback, and explicit visual baselines."""
import base64,hashlib,io,json,uuid
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from . import collaboration as c,runner,project,preview,runtime_identity
from .workbench_archive import safe_path
router=APIRouter(prefix='/api/v1/collaboration/projects')

def folder(identifier):
    path=safe_path(runner.ROOT,'data/projects/'+str(uuid.UUID(str(identifier)))+'/visuals');path.mkdir(parents=True,exist_ok=True);return path

def capture(identifier,selection=False):
    c.require(identifier)
    with preview.lock:session=preview.sessions.get(identifier)
    if not session:raise HTTPException(409,'请先打开预览并选择需要记录的页面。')
    with project.project_lock(identifier),session.lock:
        project.sources(identifier)
        frame=dict(session.frame)
        digest=project.manifest(identifier)['digest']
        if digest!=session.source_digest:raise HTTPException(409,'源码已变化，请重新构建并打开预览。')
        from . import tools
        if not tools.successful_validation(identifier,'project.build',digest):raise HTTPException(409,'缺少当前源码与环境的构建证据，请先重新构建。')
        environment=runtime_identity.require(identifier)
        if environment!=session.environment:raise HTTPException(409,'预览运行环境已变化，请重新打开。')
        if selection and not frame.get('selection'):raise HTTPException(409,'请先开启点选模式并选择页面元素。')
        return {**frame,'source_digest':digest,'environment':environment,'preview_policy':hashlib.sha256((runner.ROOT/'runtime/preview-worker.cjs').read_bytes()).hexdigest()}

def save_image(identifier,name,encoded):
    from PIL import Image
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)>4*1024**2:raise HTTPException(409,'截图超过4MiB上限。')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='PNG' or im.size!=(1000,720):raise HTTPException(409,'预览截图尺寸或格式异常。')
    directory=folder(identifier)
    if sum(p.stat().st_size for p in directory.glob('*.png'))+len(raw)>32*1024**2:raise HTTPException(409,'本项目视觉证据达到32MiB上限。')
    path=safe_path(runner.ROOT,str((directory/(name+'.png')).relative_to(runner.ROOT)));path.write_bytes(raw);return name

def metadata(frame):return {k:frame[k] for k in ('source_digest','environment','preview_policy','width','height','route')}

class NoteInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    comment:str=Field(min_length=3,max_length=1200)
    comparison_id:uuid.UUID|None=None

@router.post('/{identifier}/notes')
def add_note(identifier:uuid.UUID,payload:NoteInput):
    rid=str(identifier);frame=capture(rid,not payload.comparison_id);comparison=None
    if payload.comparison_id:
        comparison=next((v for v in c.records(rid,'comparison') if v['id']==str(payload.comparison_id)),None)
        if not comparison or comparison['current']['source_digest']!=frame['source_digest']:raise HTTPException(409,'视觉比较记录不存在或已过期。')
    if len(c.records(rid,'note'))>=40:raise HTTPException(409,'项目意见达到40条上限。')
    selected=frame.get('selection') or {};text=selected.get('text','')[:120]
    candidates=[path for path,body in project.sources(rid).items() if text and text in body][:8]
    value={'id':str(uuid.uuid4()),'comment':payload.comment,'selection':selected,'candidates':candidates,'mapping':'文本匹配候选，需开发智能体读取核对','created_at':c.now(),**metadata(frame),'comparison_id':str(payload.comparison_id) if comparison else None}
    value['image']=save_image(rid,value['id'],frame['image']);return c.insert(rid,'note',value)

@router.get('/{identifier}/evidence')
def evidence(identifier:uuid.UUID):
    rid=str(identifier);root=c.lineage(rid)
    project.sources(rid)
    digest=project.manifest(rid)['digest'];environment=runtime_identity.current(rid)
    policy=hashlib.sha256((runner.ROOT/'runtime/preview-worker.cjs').read_bytes()).hexdigest()
    comparisons=[{**v,'current_evidence':v['current']['source_digest']==digest and environment is not None and v['current']['environment']==environment and v['current']['preview_policy']==policy} for v in c.records(rid,'comparison')]
    return {'notes':c.records(rid,'note'),'baselines':c.records(root,'baseline'),'comparisons':comparisons}

def freeze_notes(parent,ids):
    if not ids:return []
    if not parent:raise HTTPException(422,'页面意见必须关联原项目。')
    project.sources(str(parent))
    notes={x['id']:x for x in c.records(str(parent),'note')};digest=project.manifest(str(parent))['digest'];result=[]
    for identifier in ids:
        item=notes.get(str(identifier))
        if not item or item['source_digest']!=digest:raise HTTPException(409,'页面意见不属于原项目或源码已变化，请重新点选。')
        result.append({k:v for k,v in item.items() if k not in {'environment','preview_policy'}})
    return result

class BaselineInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=100)
    confirmed:bool=False

@router.post('/{identifier}/baselines')
def baseline(identifier:uuid.UUID,payload:BaselineInput):
    if not payload.confirmed:raise HTTPException(422,'请明确确认当前页面作为比较基线。')
    rid=str(identifier);scope=c.lineage(rid)
    if len(c.records(scope,'baseline'))>=20:raise HTTPException(409,'项目谱系基线达到20条上限。')
    frame=capture(rid);value={'id':str(uuid.uuid4()),'run_id':rid,'label':payload.label,'created_at':c.now(),**metadata(frame)}
    value['image']=save_image(rid,value['id'],frame['image']);return c.insert(scope,'baseline',value,20)

class CompareInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    baseline_id:uuid.UUID
    tolerance:int=Field(default=16,ge=0,le=64)


def difference(left,right,tolerance):
    from PIL import Image,ImageChops
    a=Image.open(left).convert('RGB');b=Image.open(right).convert('RGB')
    if a.size!=b.size:raise HTTPException(409,'截图尺寸不同，不能比较。')
    delta=ImageChops.difference(a,b);mask=delta.convert('L').point(lambda p:255 if p>tolerance else 0)
    ratio=sum(mask.histogram()[1:])/(a.width*a.height)
    overlay=b.copy();overlay.paste(Image.new('RGB',b.size,'#ff3355'),mask=mask);out=io.BytesIO();overlay.save(out,format='PNG')
    return ratio,base64.b64encode(out.getvalue()).decode()

@router.post('/{identifier}/compare')
def compare(identifier:uuid.UUID,payload:CompareInput):
    rid=str(identifier);frame=capture(rid);base=next((x for x in c.records(c.lineage(rid),'baseline') if x['id']==str(payload.baseline_id)),None)
    if not base:raise HTTPException(404,'基线不存在。')
    if any(base[k]!=frame[k] for k in ('route','width','height','environment','preview_policy')):raise HTTPException(409,'页面、尺寸或固定环境不同，不能进行有效视觉比较。')
    if len(c.records(rid,'comparison'))>=20:raise HTTPException(409,'本项目比较记录达到20条上限。')
    value={'id':str(uuid.uuid4()),'baseline':base,'current':metadata(frame),'created_at':c.now(),'tolerance':payload.tolerance}
    value['image']=save_image(rid,value['id'],frame['image'])
    ratio,png=difference(folder(base['run_id'])/(base['image']+'.png'),folder(rid)/(value['image']+'.png'),payload.tolerance)
    value.update(changed_ratio=round(ratio,6),diff_image=save_image(rid,value['id']+'-diff',png),meaning='仅像素变化；页面数据、焦点、滚动和动态内容也可能产生差异，不等于缺陷或验收结论。')
    return c.insert(rid,'comparison',value,20)

@router.get('/{identifier}/images/{name}')
def image(identifier:uuid.UUID,name:str):
    c.require(identifier)
    import re
    if not re.fullmatch(r'[0-9a-f-]{36}(?:-diff)?',name):raise HTTPException(404,'图像不存在。')
    path=safe_path(runner.ROOT,'data/projects/'+str(identifier)+'/visuals/'+name+'.png')
    if not path.is_file():raise HTTPException(404,'图像不存在。')
    return FileResponse(path,media_type='image/png')
