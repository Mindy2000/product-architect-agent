"""Trusted manifests and recoverable edits for model-managed project files."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from . import runner

from .scope import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from .dependencies import APP_IMPORTS, TEST_IMPORTS
REQUIRED_FILES = {'src/App.tsx', 'src/main.tsx', 'src/style.css'}
PROTECTED_FILES = {'src/pa-crud-client.ts','src/vite-env.d.ts', 'tests/acceptance.spec.ts', 'tests/preview.spec.ts'}
_lock_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


class ProjectError(runner.SandboxError):
    pass


class TextEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    before: str = Field(min_length=1, max_length=65536)
    after: str = Field(max_length=65536)


class FileChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['create', 'replace', 'patch', 'delete']
    path: str = Field(min_length=1, max_length=180)
    expected_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    content: str | None = Field(default=None, max_length=65536)
    edits: list[TextEdit] = Field(default_factory=list, max_length=30)


def project_lock(run_id: str):
    with _lock_guard:
        return _locks.setdefault(str(uuid.UUID(run_id)), threading.RLock())


def metadata_dir(run_id: str) -> Path:
    path = runner.ROOT / 'data' / 'projects' / str(uuid.UUID(run_id))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def managed_path(name: str) -> bool:
    if not isinstance(name, str) or not 1 <= len(name) <= 180 or '\\' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or len(path.parts) > 8:
        return False
    if any(not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', part) for part in path.parts):
        return False
    if name in PROTECTED_FILES or path.parts[:2] == ('tests', 'standard'):
        return False
    if name=='backend/schema.json':return True
    return path.parts[0] in {'src', 'tests'} and path.suffix in {'.ts', '.tsx', '.css', '.json'}


def safe_file(run_id: str, name: str, *, editable: bool = True) -> Path:
    if editable and not managed_path(name):
        raise ProjectError('文件路径不在受控源码目录内，或属于框架保留文件。')
    if not editable and name not in runner.framework_files(run_id) and not managed_path(name):
        raise ProjectError('该文件不可读取。')
    workspace = runner.workspace_for(run_id)
    target = workspace.joinpath(*PurePosixPath(name).parts)
    if workspace not in target.resolve().parents:
        raise ProjectError('文件路径越出任务工作区。')
    current = workspace
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink():
            raise ProjectError('不允许通过符号链接访问项目文件。')
    return target


def digest(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def source_digest(files: dict[str, str]) -> str:
    return digest(json.dumps({name: digest(content) for name, content in sorted(files.items())}, sort_keys=True))


def validate_sources(files: dict[str, str]) -> None:
    if len(files) > MAX_FILES or not REQUIRED_FILES <= files.keys():
        raise ProjectError('项目须保留三个入口文件，且受控源文件不能超过128个。')
    total = 0
    for name, content in files.items():
        if not managed_path(name) or not isinstance(content, str):
            raise ProjectError('源码清单包含不可写路径。')
        size = len(content.encode('utf-8'))
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ProjectError('源码超过单文件64KiB或总计2MiB上限。')
        if name.endswith('.json'):
            try:
                value=json.loads(content)
                if name=='backend/schema.json':
                    from .crud_schema import CrudSchema
                    CrudSchema.model_validate(value)
            except ValueError as error: raise ProjectError('JSON数据文件格式不完整。') from error
        for pattern in (r'\b(?:fetch|eval|Function|require)\s*\(', r'\b(?:XMLHttpRequest|WebSocket)\b', r'\bprocess\s*\.', r'\bimport\s*\.\s*meta\s*\.\s*env', r'\b(?:from|import)\s*[\(]?\s*[\'"](?:node:|https?:|/|fs[\'"]|child_process)'):
            if re.search(pattern, content):
                raise ProjectError('代码触及联网、动态执行、系统模块或环境变量限制。')
        if name.startswith('src/') and re.search(r'https?://', content, re.I):
            raise ProjectError('前端源文件不能引用外部网络资源。')
        if name.startswith('tests/') and re.search(r'https?://(?!127\.0\.0\.1:4173(?:/|[\'"\s]))', content):
            raise ProjectError('浏览器测试只能访问本地预览。')
        imports = re.findall(r'(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)[\'"]([^\'"]+)[\'"]', content)
        for imported in imports:
            if imported.startswith('.'):
                combined = PurePosixPath(name).parent / imported
                parts = []
                for part in combined.parts:
                    if part == '..':
                        if not parts: raise ProjectError('相对导入越出项目。')
                        parts.pop()
                    elif part != '.': parts.append(part)
                if not parts or parts[0] not in {'src', 'tests'}:
                    raise ProjectError('相对导入只能指向源码和测试目录。')
            elif imported not in (APP_IMPORTS | TEST_IMPORTS):
                raise ProjectError('导入的依赖不在固定依赖列表内。')
            if name.startswith('src/') and imported == '@playwright/test':
                raise ProjectError('测试依赖不能用于前端源文件。')


def framework_hashes(run_id: str) -> dict[str, str]:
    result = {}
    for name in runner.framework_files(run_id):
        if managed_path(name): continue
        path = safe_file(run_id, name, editable=False)
        if not path.is_file() or path.stat().st_size > 512 * 1024:
            raise ProjectError('框架配置文件缺失或大小异常。')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _manifest(files: dict[str, str], revision: int, framework: dict) -> dict:
    return {'format': 2, 'revision': revision, 'framework': framework, 'digest': source_digest(files), 'files': {name: {'sha256': digest(text), 'bytes': len(text.encode('utf-8'))} for name, text in sorted(files.items())}}


def manifest(run_id: str) -> dict:
    with project_lock(run_id):
        path = metadata_dir(run_id) / 'manifest.json'
        if not path.exists():
            # Adopt only legacy framework-owned paths, never scan arbitrary files.
            files = {}
            for name in sorted(runner.ALLOWED_MODEL_PATHS | {'backend/schema.json'}):
                target = safe_file(run_id, name)
                if target.is_file():
                    if target.stat().st_size > MAX_FILE_BYTES: raise ProjectError('原有源码过大。')
                    files[name] = target.read_text(encoding='utf-8')
            if not REQUIRED_FILES <= files.keys():
                raise ProjectError('项目入口文件尚未就绪。')
            atomic_json(path, _manifest(files, 0, framework_hashes(run_id)))
        return json.loads(path.read_text(encoding='utf-8'))


def sources(run_id: str) -> dict[str, str]:
    with project_lock(run_id):
        recover_pending(run_id)
        declared = manifest(run_id)
        if declared.get('framework') != framework_hashes(run_id):
            raise ProjectError('框架配置被更改，请恢复项目后继续。')
        files = {}
        for name, entry in declared['files'].items():
            path = safe_file(run_id, name)
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                raise ProjectError('受控源文件缺失或大小异常。')
            content = path.read_text(encoding='utf-8')
            if digest(content) != entry['sha256']:
                raise ProjectError('源码已在工具协议之外发生变化，需恢复版本后继续。')
            files[name] = content
        from .fullstack import validate_contract
        validate_contract(run_id,files)
        return files


def import_sources(run_id: str, files: dict[str, str]) -> dict:
    """Initialize a trusted manifest after copying a parent's declared files."""
    validate_sources(files)
    from .fullstack import validate_contract
    validate_contract(run_id,files)
    with project_lock(run_id):
        for name, content in files.items(): runner._write_file(runner.workspace_for(run_id), name, content)
        value = _manifest(files, 0, framework_hashes(run_id))
        atomic_json(metadata_dir(run_id) / 'manifest.json', value)
        return value


def _snapshot(run_id: str, operation_id: str) -> str:
    workspace = runner.workspace_for(run_id)
    title = 'Tool snapshot ' + operation_id
    head_title = runner._git(workspace, ['log', '-1', '--format=%s']).stdout.strip()
    if head_title != title:
        runner.stage_project(run_id)
        runner._git(workspace, ['commit', '--quiet', '--allow-empty', '-m', title])
    return runner._git(workspace, ['rev-parse', 'HEAD']).stdout.strip()


def apply_changes(run_id: str, changes: list[FileChange], operation_id: str) -> dict:
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', operation_id):
        raise ProjectError('操作标识格式无效。')
    with project_lock(run_id):
        journal_path = metadata_dir(run_id) / ('edit-' + operation_id + '.json')
        fingerprint = digest(json.dumps([change.model_dump() for change in changes], sort_keys=True, ensure_ascii=False))
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding='utf-8'))
            if journal['fingerprint'] != fingerprint: raise ProjectError('同一操作标识不能用于不同变更。')
            if journal.get('result'): return {**journal['result'], 'replayed': True}
        else:
            before = sources(run_id)
            after = dict(before)
            if not changes or len(changes) > MAX_FILES * 2: raise ProjectError('一次变更必须包含1至256个文件操作。')
            seen = set()
            for change in changes:
                name = change.path
                safe_file(run_id, name)
                if name in seen: raise ProjectError('同一批变更中不能重复操作同一文件。')
                seen.add(name)
                if change.operation == 'create':
                    if name in before or safe_file(run_id, name).exists() or change.expected_sha256 is not None:
                        raise ProjectError('新建文件已存在，或携带了不适用的版本标识。')
                    if change.content is None or change.edits: raise ProjectError('新建操作需要content且不能同时包含edits。')
                    after[name] = change.content
                else:
                    if name not in before or change.expected_sha256 != digest(before[name]):
                        raise ProjectError('文件版本冲突；请重新读取文件并使用最新sha256。')
                    if change.operation == 'delete':
                        if change.content is not None or change.edits: raise ProjectError('删除不能附带内容。')
                        after.pop(name)
                    elif change.operation == 'replace':
                        if change.content is None or change.edits: raise ProjectError('替换操作需要content且不能同时包含edits。')
                        after[name] = change.content
                    else:
                        if not change.edits or change.content is not None: raise ProjectError('局部修改需要edits且不能附带完整content。')
                        content = before[name]
                        for edit in change.edits:
                            if content.count(edit.before) != 1: raise ProjectError('局部修改的before必须在文件中唯一匹配。')
                            content = content.replace(edit.before, edit.after, 1)
                        after[name] = content
            validate_sources(after)
            from .fullstack import validate_contract
            validate_contract(run_id,after)
            revision = manifest(run_id)['revision'] + 1
            journal = {'fingerprint': fingerprint, 'before': before, 'after': after, 'revision': revision, 'framework': manifest(run_id)['framework'], 'changes':[change.model_dump() for change in changes]}
            atomic_json(journal_path, journal)
        if journal['framework'] != framework_hashes(run_id):
            raise ProjectError('恢复变更前发现框架配置异常，已停止覆盖。')
        # Recovery only rolls forward paths whose current value is the recorded before/after value.
        for name in set(journal['before']) | set(journal['after']):
            path = safe_file(run_id, name)
            current = path.read_text(encoding='utf-8') if path.is_file() else None
            if current not in (journal['before'].get(name), journal['after'].get(name)):
                raise ProjectError('恢复变更时检测到第三方修改，已停止覆盖。')
        for name in set(journal['before']) | set(journal['after']):
            path = safe_file(run_id, name)
            if name not in journal['after']:
                if path.exists(): path.unlink()
            elif not path.exists() or path.read_text(encoding='utf-8') != journal['after'][name]:
                runner._write_file(runner.workspace_for(run_id), name, journal['after'][name])
        value = _manifest(journal['after'], journal['revision'], journal['framework'])
        atomic_json(metadata_dir(run_id) / 'manifest.json', value)
        result = {'manifest': value, 'changed_paths': [change.path for change in changes], 'commit': _snapshot(run_id, operation_id)}
        journal['result'] = result
        atomic_json(journal_path, journal)
        return result


def recover_pending(run_id: str) -> None:
    for path in sorted(metadata_dir(run_id).glob('edit-*.json')):
        journal = json.loads(path.read_text(encoding='utf-8'))
        if not journal.get('result'):
            changes = [FileChange.model_validate(item) for item in journal['changes']]
            apply_changes(run_id, changes, path.stem.removeprefix('edit-'))
