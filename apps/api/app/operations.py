"""Local audit and resource visibility; cleanup is limited to expired recovery uploads."""
import json,os,sqlite3,time,uuid,hashlib
from pathlib import Path
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
from . import runner
from .process_control import run_bounded
from .security_policy import redact
router=APIRouter(prefix='/api/v1/operations')


def connect():
    path=runner.ROOT/'data'/'operations.sqlite3';path.parent.mkdir(exist_ok=True)
    conn=sqlite3.connect(path,timeout=20);conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT DEFAULT CURRENT_TIMESTAMP,method TEXT,path TEXT,status INTEGER)')
    conn.execute('CREATE TABLE IF NOT EXISTS cleanup_plans(id TEXT PRIMARY KEY,created REAL,value_json TEXT)')
    conn.execute('PRAGMA user_version=1');conn.commit();return conn


def audit(method,path,status):
    conn=connect()
    try:conn.execute('INSERT INTO audit(method,path,status) VALUES (?,?,?)',(method,redact(path)[:500],status));conn.commit()
    finally:conn.close()


@router.get('/audit')
def events():
    conn=connect()
    try:return {'items':[dict(row) for row in conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]}
    finally:conn.close()


def size(root):
    total=0;count=0
    if root.is_symlink():return {'bytes':0,'files':0,'warning':'目录为链接，未读取'}
    for directory,subdirs,names in os.walk(root,followlinks=False):
        subdirs[:]=[name for name in subdirs if not (Path(directory)/name).is_symlink()]
        for name in names:
            file=Path(directory)/name
            if file.is_symlink():continue
            try:total+=file.stat().st_size;count+=1
            except OSError:continue
    return {'bytes':total,'files':count}


@router.get('/storage')
def storage():
    roots={'公开网页研究':runner.ROOT/'data'/'research','业务数据备份':runner.ROOT/'data'/'application-backups','业务数据库':runner.ROOT/'data'/'applications','源码工作区':runner.ROOT/'generated-projects','交付文件':runner.ROOT/'outputs','知识库':runner.ROOT/'data'/'knowledge','恢复暂存':runner.ROOT/'data'/'recovery','Git历史':runner.ROOT/'data'/'git'}
    return {'items':[{'name':name,**size(path)} for name,path in roots.items()],
        'retention':'仅过期恢复暂存可通过此面板清理；项目源码、交付、知识库、业务数据库及Git不自动删除。'}


@router.post('/cleanup-preview')
def cleanup_preview():
    from .recovery import ROOT
    files=[]
    if not ROOT.is_symlink() and ROOT.exists():
        for file in ROOT.glob('*.zip'):
            if file.is_symlink() or time.time()-file.stat().st_mtime<=86400:continue
            try:uuid.UUID(file.stem)
            except ValueError:continue
            files.append({'name':file.name,'bytes':file.stat().st_size,'mtime':file.stat().st_mtime})
    token=str(uuid.uuid4());conn=connect()
    try:conn.execute('INSERT INTO cleanup_plans VALUES (?,?,?)',(token,time.time(),json.dumps(files)));conn.commit()
    finally:conn.close()
    return {'token':token,'files':files,'bytes':sum(item['bytes'] for item in files)}


class Cleanup(BaseModel):token:uuid.UUID


@router.post('/cleanup-apply')
def cleanup_apply(payload:Cleanup):
    from .recovery import ROOT
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM cleanup_plans WHERE id=?',(str(payload.token),)).fetchone()
        if not row or time.time()-row['created']>600:raise HTTPException(409,'清理预览过期，请重新预览。')
        items=json.loads(row['value_json'])
        if ROOT.is_symlink():raise HTTPException(409,'目录异常。')
        for item in items:
            file=ROOT/item['name']
            if file.is_symlink() or not file.is_file() or file.stat().st_mtime!=item['mtime'] or file.stat().st_size!=item['bytes']:raise HTTPException(409,'文件已变化，请重新预览。')
        for item in items:(ROOT/item['name']).unlink()
        conn.execute('DELETE FROM cleanup_plans WHERE id=?',(str(payload.token),));conn.commit()
        return {'deleted':len(items)}
    finally:conn.close()


@router.get('/queue')
def queue():
    from . import store
    return {'concurrent_limit':1,'restart_policy':'重启后中断，不自动恢复付费调用','items':[{'run_id':item['run_id'],'status':item.get('status'),'stage':item.get('stage')} for item in store.list_runs(1000) if item.get('status') in {'queued','running'}]}


@router.get('/integrity')
def integrity():
    from . import store
    invalid=[]
    with store.db_connection() as conn:
        for row in conn.execute('SELECT id,state FROM runs'):
            try:
                state=json.loads(row['state'])
                if not isinstance(state,dict) or state.get('run_id')!=row['id']:raise ValueError()
            except (ValueError,TypeError):invalid.append(row['id'])
        versions=[dict(row) for row in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
    return {'corrupt_run_ids':invalid,'schema_migrations':versions,'policy':'只显示问题；不自动覆盖损坏记录。'}


def orphan_containers():
    import subprocess
    from .container_owner import PROJECT
    try:
        result=run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'ps','-aq','--filter','label=pa.app=product-architect','--filter','label=pa.project='+PROJECT],timeout=10,check=True)
        ids=result.stdout.split()
        if not ids:return []
        details=run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'inspect',*ids],timeout=10,check=True)
        candidates=[]
        for item in json.loads(details.stdout):
            owner=(item.get('Config',{}).get('Labels') or {}).get('pa.owner','')
            if not owner.isdigit() or int(owner)<=1:continue
            name=item.get('Name','').lstrip('/')
            if not (name.startswith('pa-phase-') or name.startswith('pa-preview-')):continue
            if item.get('Config',{}).get('Image') not in {runner.WORKER_IMAGE,'product-architect-fullstack:1.0'}:continue
            import psutil
            if not psutil.pid_exists(int(owner)):candidates.append({'id':item['Id'],'name':item['Name'].lstrip('/'),'owner_pid':int(owner)})
        return candidates
    except (OSError,subprocess.SubprocessError,ValueError):raise HTTPException(409,'无法检查本地容器，请确认隔离环境已启动。')


@router.get('/orphan-containers')
def containers():return {'items':orphan_containers(),'policy':'只列出带本项目标签、且所属API进程已不存在的容器。'}


class ContainerCleanup(BaseModel):
    ids:list[str]


@router.post('/orphan-containers/cleanup')
def remove_containers(payload:ContainerCleanup):
    import subprocess
    candidates={item['id'] for item in orphan_containers()}
    if len(payload.ids)>50 or not set(payload.ids)<=candidates:raise HTTPException(409,'容器状态已变化，请重新检查。')
    if payload.ids:
        run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'rm','-f',*payload.ids],timeout=20,check=True)
    return {'removed':len(payload.ids)}
