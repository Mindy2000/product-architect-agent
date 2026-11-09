import uuid
import asyncio
import json
from threading import Lock, BoundedSemaphore
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from .security_policy import LocalSafetyMiddleware, MAX_QUEUED_RUNS, redact, safe_output, status as security_status

from .graph import build_graph
from .model import SUPPORTED_MODELS, BudgetExceeded, ModelCallFailed, ModelNotConfigured, budget_status
from .runner import SandboxError, worker_image_available
from . import store, workbench, regression, acceptance
from . import designs, references, knowledge, library, history, maintenance, operations, dify, dependencies, source_import, model_capabilities, vision, fullstack, application_storage, research

app = FastAPI(title="Product Architect Agent", version="0.6.0", docs_url=None, redoc_url=None, openapi_url=None)
from . import collaboration,page_feedback,capability_templates,evaluation
from . import execution_replay
app.include_router(execution_replay.router)
from . import custom_templates
app.include_router(custom_templates.router)
from . import collaboration_lifecycle
app.include_router(collaboration_lifecycle.router)
app.include_router(collaboration.router)
app.include_router(page_feedback.router)
app.include_router(capability_templates.router)
app.include_router(evaluation.router)
app.include_router(workbench.router)
app.include_router(acceptance.router)
app.include_router(regression.router)
app.include_router(designs.router)
app.include_router(references.router)
app.include_router(knowledge.router)
app.include_router(library.router)
app.include_router(history.router)
app.include_router(maintenance.router)
app.include_router(operations.router)
app.include_router(dify.router)
app.include_router(dependencies.router)
app.include_router(source_import.router)
app.include_router(vision.router)
app.include_router(fullstack.router)
app.include_router(application_storage.router)
app.include_router(research.router)
execution_slots=BoundedSemaphore(1)
async_execution_queue=asyncio.Semaphore(1)


@app.middleware("http")
async def audit_operations(request:Request,call_next):
    response=await call_next(request)
    if request.method in {"POST","PUT","DELETE"}:
        try:operations.audit(request.method,request.url.path,response.status_code)
        except Exception:pass  # Audit availability must not turn a completed mutation into a failed retry.
    return response

@app.exception_handler(dify.DifyError)
async def dify_error(request:Request,error:Exception):
    return JSONResponse(status_code=409,content={'detail':str(error)})


@app.exception_handler(store.CorruptRunRecord)
async def corrupt_run_error(request:Request,error:Exception):
    return JSONResponse(status_code=409,content={'detail':'运行记录损坏；原数据保留，未自动覆盖。请查看本地数据面板。'})


workflow = None
active_runs: set[str] = set()
active_runs_lock = Lock()
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = (ROOT / "outputs").resolve()
TERMINAL_STATES = {"completed", "failed_validation", "failed", "failed_model", "failed_sandbox", "blocked_budget", "blocked_configuration", "interrupted", "paused", "cancelled"}


class RunRequest(BaseModel):
    design_id: UUID
    design_revision: int = Field(ge=1)
    model_config = {'extra':'forbid'}


def _record_failure(run_id: str, exc: Exception) -> None:
    current = store.get_run(run_id) or {"run_id": run_id, "trace": []}
    if isinstance(exc, store.CancellationRequested):
        status,summary="cancelled","用户已取消；当前阶段完成后停止，不进入修复。"
    elif isinstance(exc, BudgetExceeded):
        status, summary = "blocked_budget", str(exc)
    elif isinstance(exc, ModelNotConfigured):
        status, summary = "blocked_configuration", str(exc)
    elif isinstance(exc, ModelCallFailed):
        status, summary = "failed_model", str(exc)
    elif isinstance(exc, SandboxError):
        status, summary = "failed_sandbox", str(exc)
    else:
        status, summary = "failed", "本地工作流遇到未处理错误；密钥和异常详情已隐藏。"
    summary = redact(summary)
    can_resume = False
    pending_nodes: list[str] = []
    if workflow is not None:
        try:
            snapshot = workflow.get_state({"configurable": {"thread_id": run_id}})
            pending_nodes = list(snapshot.next) if snapshot else []
            can_resume = bool(pending_nodes)
        except Exception:
            pass
    if isinstance(exc,store.CancellationRequested):can_resume=False
    if current.get("repair_round", 0) >= 3 and "repair" in pending_nodes:
        can_resume = False
    trace = current.get("trace", []) + [{"stage": current.get("stage", "workflow"), "status": status, "at": datetime.now(timezone.utc).isoformat(), "summary": summary}]
    failure={**current, "status": status, "can_resume": can_resume, "last_error": summary, "trace": trace}
    if current.get('orchestration_version')==1:
        failure['review_status']='stopped' if current.get('review_status')=='in_progress' else current.get('review_status','not_started')
        failure['validation_ok']=False
        if isinstance(exc,SandboxError):failure.update(build_status='unverified',test_status='unverified')
        from . import runner
        try:
            if runner.workspace_for(run_id).is_dir():failure['artifact_paths']=runner.deliver(run_id,failure)
        except Exception:
            # Do not bypass source/path checks if export itself is unsafe or unavailable.
            failure['export_error']='已有源码暂无法安全导出，原文件保留，请查看本机诊断。'
    store.update_run(run_id, failure)


def _run_in_background(run_id: str, initial_state: dict | None = None) -> None:
    execution_slots.acquire()
    try:
        if store.cancellation(run_id):raise store.CancellationRequested()
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 256}
        if initial_state is None:
            from langgraph.types import Command
            snapshot = workflow.get_state(config)
            interrupted = any(task.interrupts for task in snapshot.tasks)
            workflow.invoke(Command(resume=True) if interrupted else None, config=config)
        else:
            workflow.invoke(initial_state, config=config)
    except Exception as exc:
        _record_failure(run_id, exc)
    finally:
        execution_slots.release()
        with active_runs_lock:
            active_runs.discard(run_id)


async def _dispatch_in_background(run_id:str,initial_state:dict|None=None):
    # Wait asynchronously so queued jobs do not occupy FastAPI's request thread pool.
    async with async_execution_queue:
        await asyncio.to_thread(_run_in_background,run_id,initial_state)


@app.on_event("startup")
def startup():
    global workflow
    store.connect().close()
    workbench.jobs.recover(workbench.root())
    store.interrupt_active_runs()
    designs.recover_interrupted()
    library.recover_interrupted()
    workflow = build_graph()
    for run in store.list_runs():
        if run.get("status") != "interrupted":
            continue
        try:
            snapshot = workflow.get_state({"configurable": {"thread_id": run["run_id"]}})
            next_nodes = list(snapshot.next) if snapshot else []
            run["can_resume"] = bool(next_nodes) and not (run.get("repair_round", 0) >= 3 and "repair" in next_nodes)
        except Exception:
            run["can_resume"] = False
        store.update_run(run["run_id"], run)


@app.on_event("shutdown")
def shutdown():
    workbench.jobs.shutdown(workbench.root())
    from .preview import close_all
    close_all()
    if workflow is not None:
        workflow.checkpointer.conn.close()


@app.get("/api/v1/models/capabilities")
def model_catalog():
    return model_capabilities.catalog()


@app.get("/health")
def health():
    return {"status": "ok", "service": "product-architect-agent"}


@app.get("/api/v1/capabilities")
def capabilities():
    from .model import _get_settings
    settings = _get_settings()
    return {
        "application_version": app.version,
        "application_types": ["frontend","fullstack_crud"],
        "business_backend": {"framework":"fastapi","database":"sqlite","generation":"controlled_crud_schema","image_prepared":worker_image_available(fullstack.IMAGE),"local_files":True,"data_backup_restore":"independent_copy"},
        "target": {"framework": "react", "language": "typescript", "max_pages": 12, "storage": "by_application_type", "storage_modes":{"frontend":"localStorage","fullstack_crud":"project_sqlite"}},
        "agent_team": {"coordinator":"deterministic","roles":["planner","developer","reviewer"],"execution":"sequential","shared_budget":True,"single_writer":"developer"},
        "workflow": ["requirements", "planning", "project_setup", "executor", "build", "browser_test", "review", "diagnosis", "repair", "replanning", "delivery"],
        "design_review_required": True,
        "public_research":research.capabilities(),
        "model_capabilities": model_capabilities.catalog(),
        "dependency_policy": {"mode":"locked_allowlist","requests_enabled":True},
        "source_import": {"enabled":True,"format":"react_source_zip","max_upload_bytes":16777216},
        "product_design_version": 1,
        "reference_materials": {"mode":"direct_text","enabled":True,"max_selected":12},
        "rag": {"mode":"dify_cloud_optional","network_enabled":True},
        "knowledge_library": {"enabled":True,"retrieval":"local_full_text","semantic_search":False,"ocr":library.document_capabilities()},
        "runtime_version": 3,
        "orchestration_version": 1,
        "team_policy_version": 1,
        "collaboration_version": 1,
        "tool_protocol_version": 1,
        "file_policy": {"max_files":128,"max_file_bytes":65536,"max_total_bytes":2097152,"editable_roots":["src","tests"]},
        "execution_defaults": {"step_limit":40,"run_budget_cny":5,"model_call_limit":80},
        "repair_round_limit": 3,
        "model_calls_enabled": settings["enabled"],
        "model_provider": settings["provider"],
        "model": settings["model"],
        "available_models": list(SUPPORTED_MODELS),
        "sandbox": {"engine": "docker", "network": "none", "runtime_image": "product-architect-worker:1.0", "ready": worker_image_available()},
    }


@app.post("/api/v1/runs")
def create_run(payload: RunRequest, background_tasks: BackgroundTasks):
    proposal = designs.get_design(str(payload.design_id))
    if not proposal or proposal['status'] != 'ready' or proposal['revision'] != payload.design_revision:
        raise HTTPException(status_code=409,detail='请先保存并确认最新产品方案。')
    payload_settings = designs.DesignRequest.model_validate({key:proposal[key] for key in designs.DesignRequest.model_fields if key in proposal})
    parent = store.get_run(str(payload_settings.parent_run_id)) if payload_settings.parent_run_id else None
    if payload_settings.parent_run_id and (not parent or not (parent.get('status') == 'completed' or (parent.get('status') == 'paused' and (parent.get('source_import') or parent.get('local_data_restored'))))):
        raise HTTPException(status_code=409,detail='原项目状态不支持追加修改。')
    run_id = str(payload.design_id)
    now = datetime.now(timezone.utc).isoformat()
    initial = {
        "run_id": run_id,
        "runtime_version": 3,
        "orchestration_version": 1,
        "team_policy_version": 1,
        "collaboration_version": 1,
        "project_rules": proposal.get("project_rules",[]),
        "selected_annotations": proposal.get("selected_annotations",[]),
        "template_id": proposal.get("template_id"),
        "template_digest": proposal.get("template_digest"),
        "design_id": run_id,
        "design_revision": proposal['revision'],
        "requirements_confirmed": True,
        "product_spec": proposal['spec'],
        "reference_snapshot": proposal.get('reference_snapshot',[]),
        "research_enabled":proposal.get("research_enabled",False),
        "web_snapshot":proposal.get("web_snapshot",[]),
        "knowledge_context": proposal.get('knowledge_context'),
        "knowledge_collection_ids": proposal.get('knowledge_collection_ids',[]),
        "acceptance_criteria": proposal['spec']['acceptance_criteria'] + (['业务数据使用独立FastAPI与SQLite，数据库升级须显式执行','产品限制在1–12个React页面内'] if proposal['spec'].get('application_type')=='fullstack_crud' else ['业务数据仅保存在浏览器 localStorage','产品限制在1–12个React页面内']),
        "execution_step_limit": payload_settings.execution_step_limit,
        "run_budget_cny": payload_settings.run_budget_cny,
        "model_call_limit": 80,
        "executor_turns": 0,
        "user_request": ((parent["user_request"] + "\n本次追加修改：\n" + payload_settings.request) if parent else payload_settings.request),
        "change_request": payload_settings.request if parent else "",
        "parent_run_id": str(payload_settings.parent_run_id) if payload_settings.parent_run_id else None,
        "model_name": payload_settings.model_name,
        "acceptance_profile": (parent.get("acceptance_profile", "generated") if parent and payload_settings.acceptance_profile == "generated" else payload_settings.acceptance_profile),
        "stage": "queued",
        "status": "queued",
        "repair_round": 0,
        "trace": [{"stage": "queued", "status": "queued", "at": now, "summary": "任务已进入本地执行队列"}],
        "created_at": now,
    }
    initial['trace'] = [{'stage':'references','status':'completed','at':now,'summary':f"本次选择 {len(proposal.get('reference_snapshot',[]))} 份文字参考资料（直接上下文，未使用RAG）",'sources':references.summaries(proposal.get('reference_snapshot',[]))}] + [
        {'stage':'product_design','status':'completed','at':entry['at'],
         'summary':{'generated':'模型生成产品方案','edited':'用户编辑产品方案'}.get(entry['action'],'产品方案修订')+f" · 修订 {entry['revision']}",
         **{key:entry[key] for key in ('model','input_tokens','output_tokens','estimated_cost_cny') if key in entry}}
        for entry in proposal['history']
    ] + [{'stage':'product_design','status':'completed','at':now,'summary':f"用户确认方案修订 {proposal['revision']}，开始开发"}] + initial['trace']
    if proposal.get('knowledge_context'):
        initial['trace'].append({'stage':'knowledge','status':'completed','at':now,'summary':f"已冻结 {len(library.sources(proposal['knowledge_context']))} 个知识库检索片段（{proposal.get('knowledge_context',{}).get('provider_id','本地全文')}）",'retrieval_id':proposal['knowledge_context'].get('retrieval_id'),'sources':library.sources(proposal['knowledge_context'])})
    if proposal.get('web_snapshot'):
        initial['trace'].append({'stage':'web_research','status':'completed','at':now,'summary':'已冻结用户选择的公开网页来源；未在确认开发时重新联网。','sources':research.summaries(proposal['web_snapshot'])})
    with active_runs_lock:
        if len(active_runs)>=MAX_QUEUED_RUNS:raise HTTPException(429,"本地任务队列已满，请等待已有任务结束。")
        designs.claim(run_id,payload.design_revision,initial)
        active_runs.add(run_id)
    background_tasks.add_task(_dispatch_in_background, run_id, initial)
    return public_run(initial)


def public_run(state: dict) -> dict:
    result = {key:value for key,value in state.items() if key not in {"reference_snapshot","knowledge_context","web_snapshot"}}
    result["web_sources"] = research.summaries(state.get("web_snapshot",[]))
    result["knowledge_sources"] = library.sources(state.get("knowledge_context"))
    result["reference_sources"] = references.summaries(state.get("reference_snapshot",[]))
    if result.get('pending_executor'):
        pending = result['pending_executor']
        result['pending_executor']={'turn':pending.get('turn'),'decision':{key:value for key,value in pending.get('decision',{}).items() if key != 'arguments'}}
    from . import team_policy
    if team_policy.enabled(state):result['review_reserve']=team_policy.reserve(state)
    if result.get('orchestration_version')==1 and result.get('review_report') and result.get('review_status') not in {'stopped','not_started'}:
        from . import team
        try:current=team.basis(state)==state.get('review_basis')
        except (SandboxError,OSError):current=False
        if not current:result['review_status']='stale'
    return result


@app.get('/api/v1/tools')
def available_tools():
    from .tools import catalog
    return {'protocol_version':1,'tools':catalog()}


@app.get('/api/v1/runs/{run_id}/knowledge-evidence')
def run_knowledge_evidence(run_id:UUID):
    state=store.get_run(str(run_id))
    if not state:raise HTTPException(404,'未找到开发任务。')
    return state.get('knowledge_context') or {'provider_id':'none','chunks':[]}


@app.get('/api/v1/runs/{run_id}/manifest')
def get_project_manifest(run_id: str):
    if not store.get_run(run_id): raise HTTPException(status_code=404,detail='Run not found')
    from . import project
    try:
        value=project.manifest(run_id)
        return {**value,'limits':{'max_files':project.MAX_FILES,'max_file_bytes':project.MAX_FILE_BYTES,'max_total_bytes':project.MAX_TOTAL_BYTES}}
    except SandboxError as error:raise HTTPException(status_code=409,detail=str(error))


@app.get('/api/v1/runs/{run_id}/tool-calls')
def get_tool_calls(run_id: str):
    if not store.get_run(run_id):raise HTTPException(status_code=404,detail='Run not found')
    from .tools import history
    result=history(run_id,100)
    for entry in result:
        receipt=entry.get('result')
        if receipt and receipt.get('data'):
            data=dict(receipt['data'])
            data.pop('content',None)
            if 'output' in data:data['output']=data['output'][-8000:]
            receipt['data']=data
    return result


@app.get("/api/v1/runs")
def list_runs():
    return [public_run(state) for state in store.list_runs()]


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str):
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return public_run(state)


@app.post("/api/v1/runs/{run_id}/resume")
def resume_run(run_id: str, background_tasks: BackgroundTasks):
    if workflow is None:
        raise HTTPException(status_code=503, detail="Workflow is not ready")
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if store.cancellation(run_id):raise HTTPException(409,"已取消任务不能继续付费执行，请创建新的修改任务。")
    if collaboration.pending(run_id):raise HTTPException(409,"请先回答待澄清问题，再继续任务。")
    snapshot = workflow.get_state({"configurable": {"thread_id": run_id}})
    if not snapshot or not snapshot.next:
        raise HTTPException(status_code=409, detail="This run has no pending checkpoint to resume")
    if state.get("repair_round", 0) >= 3 and "repair" in snapshot.next:
        store.update_run(run_id, {**state, "can_resume": False})
        raise HTTPException(status_code=409, detail="自动修复已达到 3 轮上限；本轮已停止，可导出当前源码查看问题。")
    with active_runs_lock:
        if run_id in active_runs:
            raise HTTPException(status_code=409, detail="该任务正在执行，请勿重复恢复。")
        if len(active_runs)>=MAX_QUEUED_RUNS:raise HTTPException(429,"本地任务队列已满，请稍后再试。")
        active_runs.add(run_id)
    try:
        store.update_run(run_id, {**state, "status": "queued", "can_resume": False, "last_error": ""})
    except Exception:
        with active_runs_lock:
            active_runs.discard(run_id)
        raise
    store.set_pause_requested(run_id, False)
    background_tasks.add_task(_dispatch_in_background, run_id, None)
    return {"run_id": run_id, "status": "queued"}


class RestoreRequest(BaseModel):
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")


@app.post('/api/v1/recovery/preview')
async def preview_backup(request:Request):
    from . import recovery
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>recovery.MAX_BYTES:raise HTTPException(413,'备份不能超过64MiB。')
    try:
        manifest,candidates=recovery.inspect(bytes(raw))
        token=str(uuid.uuid4());path=recovery.path(token)
        with path.open('xb') as stream:
            path.chmod(0o600);stream.write(raw)
        return {'token':token,'warnings':manifest.get('warnings',[]),'candidates':[{'run_id':item['run_id'],'commit':item['commit'],'file_count':len(item['files']),'name':item['spec'].get('product_name','项目')} for item in candidates]}
    except Exception as error:
        raise HTTPException(422,'无法导入：备份格式、校验值或源码范围不符合要求。') from error


class ImportBackup(BaseModel):
    token:UUID
    run_id:UUID
    commit:str=Field(pattern=r'^[0-9a-f]{40}$')


@app.post('/api/v1/recovery/restore')
def import_backup(payload:ImportBackup):
    if workflow is None:raise HTTPException(503,'工作流未就绪。')
    from . import recovery
    from .runner import initialize_workspace,apply_generated_files
    try:
        _,items=recovery.read(str(payload.token))
        selected=next((item for item in items if item['run_id']==str(payload.run_id) and item['commit']==payload.commit),None)
        if selected is None:raise ValueError('未知快照')
        spec=designs.ProductDesign.model_validate(selected['spec']).model_dump()
        if spec.get('application_type')=='fullstack_crud':
            from .crud_schema import CrudSchema
            spec['crud_schema']=CrudSchema.model_validate_json(selected['files']['backend/schema.json']).model_dump()
    except Exception as error:raise HTTPException(422,'快照已过期、损坏或方案不兼容，请重新预览。') from error
    new_id=str(uuid.uuid4())
    try:
        initialize_workspace(new_id,application_type=spec.get('application_type','frontend'),crud_schema=spec.get('crud_schema'))
        apply_generated_files(new_id,[{'path':name,'content':content} for name,content in selected['files'].items()],replace_all=True)
    except Exception as error:
        # Remove only resources allocated for this new, unpublished UUID.
        import shutil
        from .runner import workspace_for
        for directory in (workspace_for(new_id),ROOT/'data'/'projects'/new_id,ROOT/'data'/'git'/new_id):
            if directory.is_dir() and not directory.is_symlink():shutil.rmtree(directory)
        raise HTTPException(409,'恢复未完成，新建工作区已回退，原项目保留。') from error
    now=datetime.now(timezone.utc).isoformat()
    initial={'run_id':new_id,'runtime_version':2,'requirements_confirmed':True,'product_spec':spec,'acceptance_criteria':spec['acceptance_criteria'],
        'acceptance_profile':'generated','user_request':'从备份恢复源码 '+payload.commit,'repair_round':0,'repair_limit':0,'status':'paused','stage':'build',
        'created_at':now,'can_resume':True,'run_budget_cny':0,'model_call_limit':0,'trace':[{'stage':'restore','status':'completed','at':now,'summary':'从校验通过的备份复制源码；等待用户启动隔离构建验收，不调用模型。'}]}
    store.create_run(new_id,initial['user_request'],initial,now)
    try:workflow.update_state({'configurable':{'thread_id':new_id}},initial,as_node='generation')
    except Exception as error:
        store.update_run(new_id,{**initial,'status':'failed','can_resume':False,'last_error':'恢复检查点未建立；已保留源码，可重新导入。'})
        raise HTTPException(409,'源码已复制，但检查点保存失败；新记录已标记失败，原项目保留。') from error
    return public_run(initial)


@app.get("/api/v1/runs/{run_id}/sources")
def get_project_sources(run_id: str):
    if not store.get_run(run_id):
        raise HTTPException(status_code=404,detail="Run not found")
    from .runner import read_generated_sources
    try:return read_generated_sources(run_id)
    except SandboxError as error:raise HTTPException(status_code=409,detail=str(error))


@app.get("/api/v1/runs/{run_id}/versions")
def get_versions(run_id: str):
    if not store.get_run(run_id):
        raise HTTPException(status_code=404,detail="Run not found")
    from .runner import version_snapshots
    try:return version_snapshots(run_id)
    except (SandboxError, OSError):raise HTTPException(status_code=409,detail="该项目暂时没有可恢复的源码版本。")


@app.post("/api/v1/runs/{run_id}/restore")
def restore_version(run_id: str, payload: RestoreRequest, background_tasks: BackgroundTasks):
    parent=store.get_run(run_id)
    if not parent or parent.get("status") not in TERMINAL_STATES or not parent.get("product_spec"):
        raise HTTPException(status_code=409,detail="请等待当前任务结束后恢复版本。")
    from .runner import read_source_snapshot, initialize_workspace, apply_generated_files
    try:files=read_source_snapshot(run_id,payload.commit)
    except SandboxError as error:raise HTTPException(status_code=409,detail=str(error))
    new_id=str(uuid.uuid4())
    restored_spec=dict(parent['product_spec'])
    if restored_spec.get('application_type')=='fullstack_crud':
        from .crud_schema import CrudSchema
        restored_spec['crud_schema']=CrudSchema.model_validate_json(next(item['content'] for item in files if item['path']=='backend/schema.json')).model_dump()
    initialize_workspace(new_id,application_type=restored_spec.get('application_type','frontend'),crud_schema=restored_spec.get('crud_schema'))
    apply_generated_files(new_id,files,replace_all=True)
    now=datetime.now(timezone.utc).isoformat()
    initial={key:parent[key] for key in ("product_spec","acceptance_criteria","plan","plan_details","model_name","acceptance_profile") if key in parent}
    initial["product_spec"]=restored_spec
    initial.update(runtime_version=2,execution_step_limit=40,executor_turns=0,run_budget_cny=5,model_call_limit=80,run_id=new_id,user_request="恢复项目版本 "+payload.commit,parent_run_id=run_id,repair_round=0,repair_limit=0,status="paused",can_resume=True,stage="generation",created_at=now,trace=[{"stage":"restore","status":"completed","at":now,"summary":"从 Git 快照创建暂停副本，原项目保留；等待用户手动继续构建与验收，不调用模型。"}])
    store.create_run(new_id,initial["user_request"],initial,now)
    workflow.update_state({"configurable":{"thread_id":new_id}},initial,as_node="generation")
    # Restored source remains paused; the user explicitly resumes via the bounded queue.
    return initial


from .visual_policy import VisualPolicy

class PreviewAction(BaseModel):
    visual: VisualPolicy | None = None
    action: Literal["click","inspect","type","key","scroll","reload","snapshot","configure"]
    x: int = Field(default=0, ge=0, le=1000)
    y: int = Field(default=0, ge=-2000, le=2000)
    text: str = Field(default="", max_length=500)
    key: Literal["Enter","Tab","Backspace","Space","ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Escape"] = "Enter"


@app.post("/api/v1/runs/{run_id}/preview/open")
def open_product_preview(run_id: str):
    state=store.get_run(run_id)
    if not state or state.get("build_status") != "passed":
        raise HTTPException(status_code=409,detail="项目构建通过后才可预览。")
    from .preview import open_preview
    try:return open_preview(run_id)
    except SandboxError as error:raise HTTPException(status_code=409,detail=str(error))


@app.post("/api/v1/runs/{run_id}/preview/action")
def product_preview_action(run_id: str, payload: PreviewAction):
    if payload.action=='configure' and payload.visual is None:raise HTTPException(422,'缺少视觉设置。')
    from .preview import act
    try:return act(run_id,payload.model_dump())
    except SandboxError as error:raise HTTPException(status_code=409,detail=str(error))


@app.post("/api/v1/runs/{run_id}/preview/close")
def close_product_preview(run_id: str):
    from .preview import close_preview
    close_preview(run_id)
    return {"status":"closed"}


@app.post('/api/v1/runs/{run_id}/cancel')
def cancel_run(run_id:UUID):
    identifier=str(run_id);state=store.get_run(identifier)
    if not state:raise HTTPException(404,'项目不存在。')
    store.cancellation(identifier,True)
    with active_runs_lock:running=identifier in active_runs
    if not running or state.get('status')=='queued':store.update_run(identifier,{**state,'status':'cancelled','can_resume':False})
    return {'status':'cancel_requested' if running else 'cancelled','message':'当前阶段完成后停止，保留源码与费用记录。'}


@app.post("/api/v1/runs/{run_id}/pause")
def pause_run(run_id: str):
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if state.get("status") in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail="该任务当前无需暂停。")
    store.set_pause_requested(run_id, True)
    return {"run_id":run_id,"status":"pause_requested","message":"当前阶段完成后暂停"}


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    async def events():
        previous = None
        while not await request.is_disconnected():
            state = store.get_run(run_id)
            if state is None:
                break
            body = json.dumps(public_run(state), ensure_ascii=False)
            if body != previous:
                yield "data: " + body + "\n\n"
                previous = body
            else:
                yield ": keepalive\n\n"
            if state.get("status") in TERMINAL_STATES:
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.get("/api/v1/runs/{run_id}/checkpoint")
def get_checkpoint(run_id: str):
    if workflow is None:
        raise HTTPException(status_code=503, detail="Workflow is not ready")
    snapshot = workflow.get_state({"configurable": {"thread_id": run_id}})
    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return {"run_id": run_id, "values": snapshot.values, "next": list(snapshot.next), "checkpoint": snapshot.config.get("configurable", {}).get("checkpoint_id")}


@app.get("/api/v1/runs/{run_id}/download")
def download_source(run_id: str):
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    rel = state.get("artifact_paths", {}).get("source_zip")
    if not rel:
        raise HTTPException(status_code=409, detail="Source export is not ready")
    try:path = safe_output(rel)
    except ValueError:raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/api/v1/runs/{run_id}/artifact/{kind}")
def download_artifact(run_id: str, kind: str):
    if kind not in {"report", "git_diff", "trace", "preview", "manifest", "product_spec"}:
        raise HTTPException(status_code=404, detail="Artifact not found")
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    rel = state.get("artifact_paths", {}).get(kind)
    if not rel:
        raise HTTPException(status_code=409, detail="Artifact is not ready")
    try:path = safe_output(rel)
    except ValueError:raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path, filename=path.name)


@app.get("/api/v1/budget")
def get_budget():
    return budget_status()


@app.get('/api/v1/runs/{run_id}/web-evidence')
def run_web_evidence(run_id:UUID):
    value=store.get_run(str(run_id))
    if not value:raise HTTPException(404,'未找到开发任务。')
    return {'items':research.run_snapshot(str(run_id),value.get('web_snapshot',[])),'network_request':False}


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, error: RequestValidationError):
    # Input values and user-controlled field names never enter error responses.
    return JSONResponse(status_code=422,content={'detail':'请求格式不符合要求，请检查填写内容。'})

@app.get('/api/v1/operations/security')
def local_security_status():
    return security_status()

@app.exception_handler(workbench.archive.ArchiveError)
async def workbench_error(request:Request,error:Exception):
    return JSONResponse(status_code=409,content={'detail':str(error)})

app.add_middleware(workbench.MaintenanceMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])

# Keep access and body checks outside audit/CORS and all feature routers.
app.add_middleware(LocalSafetyMiddleware)
