"""Versioned tool protocol, durable receipts, and fixed local execution adapters."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from . import runner, project, research


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EmptyArgs(Arguments):
    pass


class ReadArgs(Arguments):
    path: str = Field(min_length=1, max_length=180)
    offset: int = Field(default=0, ge=0, le=65536)
    length: int = Field(default=12000, ge=1, le=20000)


class EvidenceArgs(Arguments):
    chunk_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class ReferenceArgs(Arguments):
    reference_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    offset: int = Field(default=0,ge=0,le=80000)
    length: int = Field(default=12000,ge=1,le=16000)


class WebEvidenceArgs(Arguments):
    source_id:str=Field(pattern=r'^[0-9a-f-]{36}$')
    offset:int=Field(default=0,ge=0,le=16000)
    length:int=Field(default=6000,ge=1,le=12000)


class ChangeArgs(Arguments):
    changes: list[project.FileChange] = Field(min_length=1, max_length=256)


class RestoreArgs(Arguments):
    commit: str = Field(pattern=r'^[0-9a-f]{40}$')


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    task_id: str
    operation_id: str
    acceptance_profile: str = 'generated'


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[Arguments]
    handler: Callable
    timeout_seconds: int
    resumable: bool = False


def connect():
    path = runner.ROOT / 'data' / 'tools.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute('''CREATE TABLE IF NOT EXISTS tool_calls (
        run_id TEXT NOT NULL, operation_id TEXT NOT NULL, task_id TEXT NOT NULL,
        tool_name TEXT NOT NULL, fingerprint TEXT NOT NULL, arguments_json TEXT NOT NULL,
        status TEXT NOT NULL, result_json TEXT, started_at TEXT NOT NULL, completed_at TEXT,
        PRIMARY KEY(run_id, operation_id))''')
    return connection


def history(run_id: str, limit: int = 100) -> list[dict]:
    connection = connect()
    try:
        rows = connection.execute('SELECT operation_id,task_id,tool_name,status,result_json,started_at,completed_at FROM tool_calls WHERE run_id=? ORDER BY started_at DESC LIMIT ?', (run_id, min(limit, 200))).fetchall()
        return [{**{key:row[key] for key in row.keys() if key != 'result_json'}, 'result': json.loads(row['result_json']) if row['result_json'] else None} for row in reversed(rows)]
    finally: connection.close()


def successful_validation(run_id: str, name: str, digest: str) -> dict | None:
    from .runtime_identity import current
    environment = current(run_id)
    if environment is None:return None
    connection = connect()
    try:
        rows = connection.execute('SELECT result_json FROM tool_calls WHERE run_id=? AND tool_name=? AND status=? ORDER BY completed_at DESC', (run_id, name, 'completed')).fetchall()
        for row in rows:
            result = json.loads(row['result_json'])
            if result.get('data', {}).get('digest') == digest and result.get('data', {}).get('environment_sha256') == environment['digest']:
                return result['data'] if result.get('ok') and result['data'].get('passed') else None
    finally: connection.close()
    return None


def _list(context, args):
    project.sources(context.run_id)
    return {'manifest': project.manifest(context.run_id), 'read_only_files': sorted(name for name in runner.framework_files(context.run_id) if not project.managed_path(name))}


def _read(context, args: ReadArgs):
    declared = project.manifest(context.run_id)
    if args.path not in declared['files'] and args.path not in runner.framework_files(context.run_id):
        raise project.ProjectError('文件不在项目清单中。')
    project.sources(context.run_id)
    path = project.safe_file(context.run_id, args.path, editable=False)
    if path.stat().st_size > 512 * 1024: raise project.ProjectError('读取文件过大。')
    content = path.read_text(encoding='utf-8')
    return {'path':args.path,'sha256':project.digest(content),'content':content[args.offset:args.offset+args.length], 'offset':args.offset,'total_characters':len(content),'editable':args.path in declared['files']}


def _knowledge(context,args:EvidenceArgs):
    from .store import get_run
    state=get_run(context.run_id) or {}
    chunk=next((item for item in (state.get('knowledge_context') or {}).get('chunks',[]) if item['chunk_id']==args.chunk_id),None)
    if chunk is None:raise project.ProjectError('只能读取本次任务已确认的知识库证据。')
    return chunk


def _reference(context, args: ReferenceArgs):
    from .store import get_run
    state=get_run(context.run_id) or {}
    item=next((item for item in state.get('reference_snapshot',[]) if item['id']==args.reference_id),None)
    if item is None:raise project.ProjectError('只能读取本次任务已确认选择的参考资料。')
    return {'reference_id':item['id'],'title':item['title'],'sha256':item['sha256'],
        'content':item['content'][args.offset:args.offset+args.length],'offset':args.offset,
        'available_characters':len(item['content']),'omitted_characters':item.get('omitted_characters',0)}


def _change(context, args: ChangeArgs):
    return project.apply_changes(context.run_id, args.changes, context.operation_id)


def _ready(context):
    files = project.sources(context.run_id)
    if not any(name.startswith('tests/') and name.endswith('.spec.ts') for name in files):
        raise project.ProjectError('构建与测试前须提供至少一个受控Playwright测试文件。')
    return project.manifest(context.run_id)['digest']


def _build(context, args):
    from . import runtime_identity
    digest = _ready(context)
    environment = runtime_identity.require(context.run_id)
    cached = successful_validation(context.run_id, 'project.build', digest)
    if cached: return {**cached, 'cached':True}
    result = runner.run_phase(context.run_id, 'build', timeout=150, image_id=environment['image_id'])
    project.sources(context.run_id)
    runtime_identity.unchanged(context.run_id,environment)
    return {'environment_sha256':environment['digest'],'digest':digest,'passed':result.exit_code == 0,'exit_code':result.exit_code,'output':result.output,'duration_seconds':result.duration_seconds}


def _test(context, args):
    from . import runtime_identity
    digest = _ready(context)
    environment = runtime_identity.require(context.run_id)
    built = successful_validation(context.run_id, 'project.build', digest)
    if not built or not built.get('passed'):
        raise project.ProjectError('请先对当前源码执行成功的project.build。')
    cached = successful_validation(context.run_id, 'project.test', digest)
    from .acceptance import suite_hash,health_hash
    from . import page_health
    expected_suite=suite_hash(context.acceptance_profile)
    if cached and cached.get('profile') == context.acceptance_profile and cached.get('suite_sha256')==expected_suite and cached.get('health_sha256')==health_hash(context.run_id) and cached.get('health_passed') is True: return {**cached, 'cached':True}
    profile = context.acceptance_profile
    if profile != 'generated':
        allowed = {'todo-v1','todo-v2','notes-v1','expenses-v1','habits-v1','reading-v1'}
        if profile not in allowed: raise project.ProjectError('未知的独立验收类型。')
        content = (runner.ROOT / 'benchmarks' / (profile + '.spec.ts')).read_text(encoding='utf-8')
        runner._write_file(runner.workspace_for(context.run_id), 'tests/acceptance.spec.ts', content)
    health_content=page_health.script(context.run_id)
    runner._write_file(runner.workspace_for(context.run_id),'tests/standard/health.spec.ts',health_content)
    result = runner.run_phase(context.run_id, 'acceptance' if profile != 'generated' else 'test', timeout=120, image_id=environment['image_id'])
    health = runner.run_phase(context.run_id,'health',timeout=60, image_id=environment['image_id'])
    project.sources(context.run_id)
    if (runner.workspace_for(context.run_id)/'tests/standard/health.spec.ts').read_text()!=health_content:raise project.ProjectError('固定基础检查文件被更改，结果未采纳。')
    runtime_identity.unchanged(context.run_id,environment)
    return {'environment_sha256':environment['digest'],'digest':digest,'profile':profile,'suite_sha256':expected_suite,'health_sha256':health_hash(context.run_id),'coverage':page_health.coverage(context.run_id),'health_passed':health.exit_code==0,'evidence_kind':'generated' if profile=='generated' else 'independent_suite','requirements_confirmed':False,'passed':result.exit_code == 0 and health.exit_code==0,'exit_code':result.exit_code or health.exit_code,'output':result.output+'\n固定页面基础检查（不代表需求验收）：\n'+health.output,'duration_seconds':result.duration_seconds+health.duration_seconds}


def _snapshot(context, args):
    project.sources(context.run_id)
    return {'commit':project._snapshot(context.run_id, context.operation_id), 'manifest':project.manifest(context.run_id)}


def _versions(context, args):
    return {'versions':runner.version_snapshots(context.run_id)}


def _restore(context, args: RestoreArgs):
    desired = {item['path']:item['content'] for item in runner.read_source_snapshot(context.run_id,args.commit)}
    journal = project.metadata_dir(context.run_id) / ('restore-' + context.operation_id + '.json')
    if journal.exists():
        changes = [project.FileChange.model_validate(item) for item in json.loads(journal.read_text())['changes']]
    else:
        before = project.sources(context.run_id)
        changes = []
        for name in sorted(before.keys() | desired.keys()):
            if before.get(name) == desired.get(name): continue
            changes.append(project.FileChange(operation='delete' if name not in desired else 'replace' if name in before else 'create',path=name,expected_sha256=project.digest(before[name]) if name in before else None,content=desired.get(name)))
        project.atomic_json(journal, {'commit':args.commit,'changes':[change.model_dump() for change in changes]})
    if not changes: return {'commit':args.commit,'manifest':project.manifest(context.run_id),'changed_paths':[]}
    return project.apply_changes(context.run_id,changes,context.operation_id)


def _route_template(context,args):
    return {'suggested_path':'src/router.tsx','content':(runner.ROOT/'templates'/'react'/'router.tsx').read_text(), 'usage':'Router routes=[{path:"/",element:<Home/>},{path:"/items/:id",element:<Detail/>}]；Link to="/items/123"；useRoute().params.id读取参数。Hash导航，不新增依赖。'}


def _request_dependency(context, args):
    from .dependencies import request_dependency
    return request_dependency(context.run_id,args)


from .dependencies import DependencyRequest


def _web_search(context,args):
    state=research.require_run(context.run_id,True)
    original=(state.get('change_request') or state.get('user_request') or '').strip()
    if len(original)>=16 and original in args.query:raise research.ResearchError('检索只允许必要关键词，不发送需求全文。')
    return research.search(args.query,args.provider,context.run_id)


def _web_read(context,args):
    try:return research.read_for_run(context.run_id,args.url)
    except ValueError:raise research.ResearchError('网址不符合公开HTTPS读取范围。') from None


def _web_evidence(context,args):
    state=research.require_run(context.run_id)
    item=next((item for item in research.run_snapshot(context.run_id,state.get('web_snapshot',[])) if item['id']==args.source_id),None)
    if not item:raise research.ResearchError('只能读取本任务已冻结的公开来源。')
    return {**item,'content':item['content'][args.offset:args.offset+args.length],'offset':args.offset}


TOOLS = {
    spec.name:spec for spec in (
        ToolSpec('web.search','仅任务允许联网时搜索免费公开目录/维基百科/GitHub公开仓库；结果摘要不等于正文。',research.SearchInput,_web_search,35,False),
        ToolSpec('web.read','读取公开HTTPS网页并冻结来源，禁止私网/登录/凭证；不执行页面脚本。',research.ReadInput,_web_read,40,False),
        ToolSpec('web.read_evidence','仅阅读本任务冻结网页快照，不联网。',WebEvidenceArgs,_web_evidence,10,True),
        ToolSpec('knowledge.read_evidence','读取已确认知识库片段与来源；不联网或上传资料。',EvidenceArgs,_knowledge,10,True),
        ToolSpec('reference.read','读取本次任务选择的文本参考资料快照，不检索外部服务。',ReferenceArgs,_reference,10,True),
        ToolSpec('project.route_template','读取本地多页面路由模板；不会自动覆盖文件。',EmptyArgs,_route_template,10,True),
        ToolSpec('project.request_dependency','查询并复用固定白名单依赖，其他版本或新包登记审查；不执行npm命令。',DependencyRequest,_request_dependency,10,True),
        ToolSpec('project.list_files','列出当前项目受控文件、SHA256和只读框架配置。',EmptyArgs,_list,10,True),
        ToolSpec('project.read_file','按路径读取当前项目文件片段，返回SHA256用于增量修改。',ReadArgs,_read,10,True),
        ToolSpec('project.apply_changes','创建、唯一文本补丁、替换或删除文件；编辑已有文件必须带最新SHA256。整批变更带恢复日志和自动Git快照。',ChangeArgs,_change,30,True),
        ToolSpec('project.build','以固定依赖在禁网Docker容器中检查TypeScript并构建。',EmptyArgs,_build,150),
        ToolSpec('project.test','在禁网容器中执行用户选定的独立验收或生成测试。须先构建当前源码。',EmptyArgs,_test,180),
        ToolSpec('git.snapshot','保存当前项目的受控源码快照。',EmptyArgs,_snapshot,20,True),
        ToolSpec('git.versions','查看当前项目可恢复的完整源码版本。',EmptyArgs,_versions,20,True),
        ToolSpec('git.restore','从本项目Git快照恢复受控源码，不修改配置或访问其他目录。',RestoreArgs,_restore,30,True),
    )
}


def catalog() -> list[dict]:
    return [{'name':spec.name,'version':1,'description':spec.description,'input_schema':spec.arguments.model_json_schema(),'timeout_seconds':spec.timeout_seconds} for spec in TOOLS.values()]


def execute(context: ToolContext, name: str, arguments: dict) -> dict:
    started = time.monotonic()
    spec = TOOLS.get(name)
    encoded = json.dumps({'tool':name,'arguments':arguments,'profile':context.acceptance_profile},ensure_ascii=False,sort_keys=True)
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    with project.project_lock(context.run_id):
        connection = connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM tool_calls WHERE run_id=? AND operation_id=?', (context.run_id,context.operation_id)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint: raise project.ProjectError('操作标识与已持久化的参数不一致。')
                if row['status'] == 'completed':
                    saved = json.loads(row['result_json'])
                    if name in {'project.build','project.test'}:
                        from . import runtime_identity, acceptance
                        environment = runtime_identity.current(context.run_id)
                        data = saved.get('data',{})
                        valid = environment is not None and data.get('environment_sha256')==environment['digest'] and data.get('digest')==project.manifest(context.run_id)['digest']
                        if name=='project.test':
                            valid = valid and data.get('health_sha256')==acceptance.health_hash(context.run_id) and data.get('suite_sha256')==acceptance.suite_hash(context.acceptance_profile)
                        if not valid:return {'ok':False,'error':{'code':'stale_validation','message':'旧验证回执的源码、环境或检查内容已变化，请以新操作重新构建与测试。'}}
                    return {**saved, 'replayed':True}
            else:
                connection.execute('INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,NULL,?,NULL)',(context.run_id,context.operation_id,context.task_id,name,fingerprint,json.dumps(arguments,ensure_ascii=False),'running',datetime.now(timezone.utc).isoformat()))
            connection.commit()
            try:
                if spec is None:
                    result = {'ok':False,'error':{'code':'unknown_tool','message':'工具未注册。'}}
                else:
                    parsed = spec.arguments.model_validate(arguments)
                    if row and not spec.resumable:
                        result = {'ok':False,'error':{'code':'interrupted_operation','message':'上一次执行结果未被保存，未自动重复运行；如仍需执行，请发起新的工具操作。'}}
                    else:
                        result = {'ok':True,'data':spec.handler(context,parsed)}
            except ValidationError:
                result = {'ok':False,'error':{'code':'invalid_arguments','message':'工具参数不符合公开Schema，请修正后重试。'}}
            except research.ResearchError as error:
                result={'ok':False,'error':{'code':'public_research_blocked','message':str(error)[:500]}}
            except runner.SandboxError as error:
                result = {'ok':False,'error':{'code':'project_conflict','message':str(error)}}
            except Exception:
                result = {'ok':False,'error':{'code':'tool_failed','message':'工具执行失败，内部异常和密钥未写入输出。'}}
            result.update(tool=name,operation_id=context.operation_id,task_id=context.task_id,duration_seconds=round(time.monotonic()-started,3))
            connection.execute('UPDATE tool_calls SET status=?,result_json=?,completed_at=? WHERE run_id=? AND operation_id=?',('completed',json.dumps(result,ensure_ascii=False),datetime.now(timezone.utc).isoformat(),context.run_id,context.operation_id))
            connection.commit()
            return result
        finally: connection.close()
