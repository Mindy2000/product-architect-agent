"""Source-bound acceptance evidence. The Agent cannot mark human reviews as passed."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from . import project, runner, store, tools, runtime_identity

router = APIRouter(prefix='/api/v1/projects')
PROFILES = {'todo-v1','todo-v2','notes-v1','expenses-v1','habits-v1','reading-v1'}
FINISHED = {'completed','failed_validation','failed','failed_model','failed_sandbox','blocked_budget','blocked_configuration','paused','interrupted','cancelled'}
NOTICE = '执行完成只表示运行链路结束。生成测试通过不等于需求全部验收；每条标准须有独立证据关联或人工确认。'


def setup(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS acceptance_reviews(run_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,basis_hash TEXT NOT NULL,entries_json TEXT NOT NULL,created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS acceptance_review_events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,revision INTEGER NOT NULL,basis_hash TEXT NOT NULL,entries_json TEXT NOT NULL,created_at TEXT NOT NULL)')


def suite_hash(profile):
    if profile not in PROFILES:return None
    path = runner.ROOT/'benchmarks'/(profile+'.spec.ts')
    if not path.is_file() or path.is_symlink():return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def health_hash(identifier):
    from .page_health import fingerprint
    return fingerprint(identifier)


def validations(identifier, digest, environment):
    if environment is None:return {}
    latest = {}
    conn = tools.connect()
    try:
        rows = conn.execute("SELECT tool_name,result_json FROM tool_calls WHERE run_id=? AND status='completed' AND tool_name IN ('project.build','project.test') ORDER BY completed_at DESC,rowid DESC",(identifier,)).fetchall()
        for row in rows:
            if row['tool_name'] in latest:continue
            try:value = json.loads(row['result_json'] or '{}');data = value.get('data',{})
            except (ValueError,TypeError):continue
            if not isinstance(data,dict) or data.get('digest')!=digest or data.get('environment_sha256')!=environment['digest']:continue
            latest[row['tool_name']] = {**data,'passed':value.get('ok') is True and data.get('passed') is True}
    finally:conn.close()
    return latest


def matrix(identifier, state=None):
    state = state or store.get_run(identifier)
    if not state:raise HTTPException(404,'项目不存在。')
    try:files = project.sources(identifier)
    except (runner.SandboxError,OSError):raise HTTPException(409,'源码未就绪或已变化，请先修复文件状态后读取验收证据。') from None
    criteria = state.get('acceptance_criteria',[])
    digest = project.source_digest(files)
    profile = state.get('acceptance_profile','generated')
    environment = runtime_identity.current(identifier)
    basis = hashlib.sha256(json.dumps({'environment':environment,'source':digest,'criteria':criteria,'profile':profile,'suite_sha256':suite_hash(profile),'health_sha256':health_hash(identifier)},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    proof = validations(identifier,digest,environment)
    build = proof.get('project.build',{});test = proof.get('project.test',{})
    independent_hash = suite_hash(profile)
    independent = bool(independent_hash and test.get('passed') and test.get('profile')==profile and test.get('suite_sha256')==independent_hash)
    test_current = bool(test.get('passed') and test.get('profile')==profile and (profile=='generated' or independent))
    with store.db_connection() as conn:
        setup(conn);row = conn.execute('SELECT * FROM acceptance_reviews WHERE run_id=?',(identifier,)).fetchone()
    saved = json.loads(row['entries_json']) if row else []
    entries = {entry['criterion']:entry for entry in saved}
    rows = []
    for index,text in enumerate(criteria):
        entry = entries.get(index)
        valid = bool(entry and row['basis_hash']==basis)
        if valid and entry['method']=='independent_suite':valid = independent and entry.get('suite_sha256')==independent_hash
        result = entry['result'] if valid else 'stale' if entry else 'unreviewed'
        candidates = [task['id'] for task in state.get('executor_tasks',[]) if index in task.get('acceptance_indices',[])]
        rows.append({'index':index,'text':text,'status':result,'method':entry['method'] if entry else None,'note':entry['note'] if entry else '', 'reviewed_at':entry.get('reviewed_at') if entry else None,'candidate_task_ids':candidates})
    passed = sum(item['status']=='passed' for item in rows)
    failed = sum(item['status']=='failed' for item in rows)
    health_current = test.get('health_passed') is True and test.get('health_sha256')==health_hash(identifier)
    technical = build.get('passed') is True and test_current and health_current
    confirmed = bool(rows) and passed==len(rows) and technical
    from .page_health import coverage
    return {'coverage':coverage(identifier),'run_id':identifier,'revision':row['revision'] if row else 0,'basis_hash':basis,'source_digest':digest,
            'review_status':'confirmed' if confirmed else 'failed' if failed else 'pending',
            'counts':{'total':len(rows),'passed':passed,'failed':failed,'unverified':len(rows)-passed-failed},
            'technical':{'environment_verified':environment is not None,'environment_sha256':environment['digest'] if environment else None,'health_passed':health_current,'build_passed':build.get('passed') is True,'test_passed':test_current,'test_kind':'independent_suite' if independent else 'generated' if test_current and profile=='generated' else 'unverified','profile':profile,'suite_sha256':independent_hash if independent else None},
            'criteria':rows,'notice':NOTICE,'manual_or_independent_confirmation_required':True,'network_requests':0}


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    criterion: int = Field(ge=0,le=63)
    method: Literal['manual','independent_suite']
    result: Literal['passed','failed']
    note: str = Field(min_length=5,max_length=1000)


class SaveReviews(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    expected_basis_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviews: list[Review] = Field(max_length=64)


@router.get('/{run_id}/acceptance')
def read_matrix(run_id: UUID):return matrix(str(run_id))


@router.put('/{run_id}/acceptance')
def save_reviews(run_id: UUID, payload: SaveReviews):
    identifier = str(run_id)
    with project.project_lock(identifier):
        state = store.get_run(identifier)
        if not state:raise HTTPException(404,'项目不存在。')
        if state.get('status') not in FINISHED:raise HTTPException(409,'请先暂停或等待开发结束，避免确认正在变化的源码。')
        current = matrix(identifier,state)
        if payload.expected_basis_hash!=current['basis_hash']:raise HTTPException(409,'源码、需求或验收方式已变化，请刷新后重新核对。')
        indices = [item.criterion for item in payload.reviews]
        if len(indices)!=len(set(indices)) or any(i>=len(current['criteria']) for i in indices):raise HTTPException(422,'验收标准序号无效或重复。')
        entries = [];now = datetime.now(timezone.utc).isoformat()
        for item in payload.reviews:
            if item.method=='independent_suite' and (not current['technical']['suite_sha256'] or item.result!='passed'):raise HTTPException(409,'当前源码没有可用的独立套件通过证据；请使用人工核对记录失败。')
            if not item.note.strip():raise HTTPException(422,'请说明实际核对操作或独立测试与该条要求的对应关系。')
            entries.append({**item.model_dump(),'reviewed_at':now,'suite_sha256':current['technical']['suite_sha256'] if item.method=='independent_suite' else None,'reviewer':'local_user'})
        with store.db_connection() as conn:
            setup(conn);conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT revision FROM acceptance_reviews WHERE run_id=?',(identifier,)).fetchone()
            if (row['revision'] if row else 0)!=payload.revision:raise HTTPException(409,'验收记录已更新，请刷新后保存。')
            args=(identifier,payload.revision+1,current['basis_hash'],json.dumps(entries,ensure_ascii=False),now)
            conn.execute('INSERT INTO acceptance_reviews VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,basis_hash=excluded.basis_hash,entries_json=excluded.entries_json,created_at=excluded.created_at',args)
            conn.execute('INSERT INTO acceptance_review_events(run_id,revision,basis_hash,entries_json,created_at) VALUES (?,?,?,?,?)',args)
    return matrix(identifier)


@router.get('/{run_id}/acceptance/export')
def export_matrix(run_id: UUID):
    return JSONResponse(matrix(str(run_id)),headers={'Content-Disposition':'attachment; filename="acceptance-evidence.json"'})
