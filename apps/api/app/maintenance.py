"""Local change records and explicit traceability; no model inference."""
import difflib
import json
from uuid import UUID
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import store,runner

router=APIRouter(prefix='/api/v1/projects')


def setup(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS maintenance (run_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,value_json TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS maintenance_events (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,revision INTEGER NOT NULL,value_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')


class Link(BaseModel):
    model_config=ConfigDict(extra='forbid')
    criterion:int=Field(ge=0)
    task_id:str=Field(min_length=1,max_length=100)
    files:list[str]=Field(default_factory=list,max_length=128)
    note:str=Field(default='',max_length=1000)


class Record(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0)
    summary:str=Field(default='',max_length=3000)
    links:list[Link]=Field(default_factory=list,max_length=200)
    page_sources:dict[str,list[str]]=Field(default_factory=dict)


def require(identifier):
    state=store.get_run(identifier)
    if not state:raise HTTPException(404,'项目不存在。')
    return state


@router.get('/{run_id}/maintenance')
def read_record(run_id:UUID):
    identifier=str(run_id);state=require(identifier)
    with store.db_connection() as conn:
        setup(conn)
        row=conn.execute('SELECT revision,value_json FROM maintenance WHERE run_id=?',(identifier,)).fetchone()
        events=conn.execute('SELECT revision,created_at FROM maintenance_events WHERE run_id=? ORDER BY id DESC LIMIT 50',(identifier,)).fetchall()
    try:source_files=sorted(runner.read_generated_sources(identifier))
    except (runner.SandboxError,OSError):source_files=[]
    parent=store.get_run(state.get('parent_run_id','')) or {}
    old=parent.get('acceptance_criteria',[]);new=state.get('acceptance_criteria',[])
    return {'revision':row['revision'] if row else 0,**(json.loads(row['value_json']) if row else {'summary':'','links':[]}),
        'page_sources':(json.loads(row['value_json']).get('page_sources',{}) if row else {}),'page_routes':[p['route'] for p in (state.get('product_spec') or {}).get('page_details',[])],'source_files':source_files,'criteria':new,'suggested_summary':(state.get('product_spec') or {}).get('summary',''),'suggested_links':[{'criterion':index,'task_id':task['id'],'files':task.get('changed_paths',[]),'note':'规划声明的关联；文件来自执行记录，不代表验收通过'} for task in state.get('executor_tasks',[]) for index in task.get('acceptance_indices',[]) if 0<=index<len(new)],'tasks':state.get('executor_tasks',[]),'criteria_added':[c for c in new if c not in old],
        'criteria_removed':[c for c in old if c not in new],'events':[dict(item) for item in events],
        'baseline_frozen':bool(state.get('requirements_confirmed')),'parent_run_id':state.get('parent_run_id')}


@router.put('/{run_id}/maintenance')
def save_record(run_id:UUID,payload:Record):
    identifier=str(run_id);state=require(identifier)
    tasks={task['id'] for task in state.get('executor_tasks',[])}
    try:files=runner.read_generated_sources(identifier)
    except runner.SandboxError:files={}
    routes={page.get('route') for page in (state.get('product_spec') or {}).get('page_details',[])}
    if len(payload.page_sources)>12 or any(route not in routes or not 1<=len(names)<=16 or any(name not in files for name in names) for route,names in payload.page_sources.items()):raise HTTPException(422,'页面映射须对应已确认路由和当前项目源码。')
    for link in payload.links:
        if link.criterion>=len(state.get('acceptance_criteria',[])) or link.task_id not in tasks or any(name not in files for name in link.files):
            raise HTTPException(422,'关联包含未知验收标准、任务或文件，请重新读取项目。')
    with store.db_connection() as conn:
        setup(conn);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT revision FROM maintenance WHERE run_id=?',(identifier,)).fetchone()
        revision=row['revision'] if row else 0
        if revision!=payload.revision:raise HTTPException(409,'维护记录已更新，请重新读取后保存。')
        value=json.dumps(payload.model_dump(exclude={'revision'}),ensure_ascii=False)
        conn.execute('INSERT INTO maintenance VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,value_json=excluded.value_json',(identifier,revision+1,value))
        conn.execute('INSERT INTO maintenance_events(run_id,revision,value_json) VALUES (?,?,?)',(identifier,revision+1,value))
    return {'revision':revision+1}


@router.get('/{run_id}/diff')
def source_diff(run_id:UUID):
    identifier=str(run_id);state=require(identifier)
    if not state.get('parent_run_id'):raise HTTPException(409,'没有父版本。')
    try:
        before=runner.read_generated_sources(state['parent_run_id']);after=runner.read_generated_sources(identifier)
    except runner.SandboxError as error:raise HTTPException(409,str(error)) from error
    result=[]
    for name in sorted(set(before)|set(after)):
        if before.get(name)==after.get(name):continue
        patch=''.join(difflib.unified_diff(before.get(name,'').splitlines(True),after.get(name,'').splitlines(True),fromfile='before/'+name,tofile='after/'+name))
        result.append({'path':name,'status':'added' if name not in before else 'deleted' if name not in after else 'modified','diff':patch})
    return {'files':result,'basis':'当前受控父子源码快照；非行为验证'}
