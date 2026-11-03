"""Inspect an explicitly uploaded React ZIP; never execute or extract its configuration."""
import hashlib
import io
import json
import stat
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from . import project, runner, store
from .dependencies import inventory
from .designs import ProductDesign

router = APIRouter(prefix='/api/v1/projects/source-import')
MAX_UPLOAD = 16 * 1024 * 1024
ROOT = runner.ROOT / 'data' / 'source-import'


def preview_path(token):
    if (runner.ROOT / 'data').is_symlink() or ROOT.is_symlink(): raise ValueError('导入目录异常。')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = ROOT / (str(uuid.UUID(str(token))) + '.json')
    if path.is_symlink(): raise ValueError('预览文件异常。')
    return path


def inspect(raw):
    if len(raw) > MAX_UPLOAD: raise ValueError('源码ZIP不能超过16MiB。')
    files = {}; ignored = 0; blocked = []; warnings = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or len({i.filename for i in entries}) != len(entries):
            raise ValueError('ZIP条目过多或重复。请移除node_modules与构建产物后重新打包。')
        if sum(i.file_size for i in entries) > 32 * 1024 * 1024: raise ValueError('ZIP展开内容超过32MiB。')
        for info in entries:
            name = info.filename.rstrip('/')
            if info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16): raise ValueError('不支持加密文件或符号链接。')
            if not name or '\\' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('ZIP包含非法路径。')
            if info.file_size > 4 * 1024 * 1024: raise ValueError('归档中有超过4MiB的单项文件，请移除大文件。')
        roots = [info.filename[:-len('package.json')] for info in entries
                 if PurePosixPath(info.filename).name == 'package.json'
                 and not any(part in {'node_modules', '.git', '__MACOSX', 'dist'} for part in PurePosixPath(info.filename).parts)]
        if len(roots) != 1: raise ValueError('请上传只有一个package.json的React应用；暂不支持多包仓库。')
        prefix = roots[0]
        manifest_info = archive.getinfo(prefix + 'package.json')
        if manifest_info.file_size > 128 * 1024: raise ValueError('package.json过大。')
        package = json.loads(archive.read(manifest_info).decode('utf-8-sig'))
        declared = package.get('dependencies', {})
        if not isinstance(declared, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in declared.items()):
            raise ValueError('依赖声明格式不支持。')
        if 'react' not in declared: raise ValueError('当前只接收React前端工程。')
        locked = {item['name']: item['version'] for item in inventory()}
        dependencies = []
        for name, version in declared.items():
            dependencies.append({'name': name, 'requested': version, 'locked': locked.get(name),
                                 'status': 'locked_candidate' if name in locked else 'unsupported'})
            if name not in locked: blocked.append('依赖不在当前白名单：' + name)
        if prefix+'backend/schema.json' in archive.namelist():blocked.append('这是全栈工程；普通React源码导入不迁移后端或数据，请使用完整项目备份恢复。')
        total = 0
        for info in entries:
            if info.is_dir() or not info.filename.startswith(prefix): ignored += 1; continue
            name = info.filename[len(prefix):]
            parts = PurePosixPath(name).parts
            if any(part.startswith('.') or part in {'node_modules', 'dist', '__MACOSX'} for part in parts):
                ignored += 1; continue
            if not parts or parts[0] not in {'src', 'tests'}: ignored += 1; continue
            if name == 'src/vite-env.d.ts' or name in project.PROTECTED_FILES:
                ignored += 1; continue
            if not project.managed_path(name):
                blocked.append('尚不支持的源码或资源路径：' + name); continue
            if info.file_size > project.MAX_FILE_BYTES: raise ValueError('单个源码超过64KiB。')
            if len(files) >= project.MAX_FILES: raise ValueError('源码文件超过128个。')
            total += info.file_size
            if total > project.MAX_TOTAL_BYTES: raise ValueError('源码合计超过2MiB。')
            files[name] = archive.read(info).decode('utf-8-sig')
        if 'src/style.css' not in files:
            files['src/style.css'] = '/* Imported project: styles may be imported by main.tsx. */\n'
            warnings.append('原工程没有src/style.css，导入时补入空样式入口；原样式文件保留。')
        try: project.validate_sources(files)
        except project.ProjectError as error: blocked.append(str(error))
        warnings.extend(['只导入src/tests内受支持的文本源码；配置、脚本、锁文件、Git、环境变量和依赖目录不导入。',
                         '构建配置由系统固定模板替换，Vite插件、路径别名、公共资源及环境变量依赖可能不兼容。',
                         '依赖使用固定镜像版本；兼容性尚未构建验证。导入和打开不会自动执行代码或调用模型。'])
    return {'files': files, 'dependencies': dependencies, 'warnings': warnings,
            'blocked': list(dict.fromkeys(blocked))[:100], 'ignored_entries': ignored,
            'archive_sha256': hashlib.sha256(raw).hexdigest(), 'created_at': time.time()}


def public(value):
    return {key: item for key, item in value.items() if key not in {'files', 'created_at'}} | {
        'file_paths': sorted(value['files']), 'file_count': len(value['files']),
        'can_import': not value['blocked'], 'expires_seconds': 86400}


@router.post('/preview')
async def preview(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_UPLOAD: raise HTTPException(413, '源码ZIP不能超过16MiB。')
    try:
        value = inspect(bytes(raw)); token = str(uuid.uuid4())
        project.atomic_json(preview_path(token), value)
        return {**public(value), 'token': token}
    except (ValueError, UnicodeError, zipfile.BadZipFile, KeyError, TypeError) as error:
        raise HTTPException(422, '无法预览：ZIP不是受支持的单一React源码工程，或超出文件与资源限制。') from error


class ImportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: uuid.UUID
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=10, max_length=500)
    routes: list[str] = Field(default_factory=lambda:['/'], min_length=1, max_length=12)


@router.post('/confirm')
def confirm(payload: ImportInput):
    with project.project_lock(str(payload.token)):
        return _confirm(payload)


def _confirm(payload):
    try:
        path = preview_path(payload.token)
        if not path.is_file() or time.time() - path.stat().st_mtime > 86400: raise ValueError('预览已过期。')
        if path.stat().st_size > 16 * 1024 * 1024: raise ValueError('预览文件超限。')
        value = json.loads(path.read_text())
        if value['blocked']: raise ValueError('兼容性报告中仍有阻断项。')
        project.validate_sources(value['files'])
        spec = ProductDesign.model_validate({
            'product_name': payload.title, 'summary': payload.summary, 'target_users': ['本地项目使用者'],
            'pages': ['导入页面 ' + str(i + 1) for i in range(len(payload.routes))],
            'page_details': [{'name': '导入页面 ' + str(i + 1), 'route': route, 'purpose': '用户填写的已有页面，行为待核对',
                              'sections': ['导入源码'], 'interactions': ['待后续方案确认']} for i, route in enumerate(payload.routes)],
            'core_features': ['保留用户导入的前端源码', '在后续已确认方案中进行维护'],
            'assumptions': ['页面路径由用户填写，不代表源码路由已核验。'],
            'non_goals': ['不导入后端或原构建脚本', '不自动执行或调用模型'],
            'acceptance_criteria': ['后续确认方案后逐项定义功能验收', '保留明确要求保留的原功能', '页面路径与用户确认的方案一致', '受控构建与交互检查通过后才标记验收'],
            'visual_direction': '沿用原工程视觉，具体效果待核对', 'data_notes': ['仅支持localStorage；现有持久化逻辑待后续核对。']}).model_dump()
    except (ValueError, KeyError, TypeError) as error: raise HTTPException(422, str(error)[:500]) from error
    identifier = str(payload.token)
    existing = store.get_run(identifier)
    if existing:
        if not existing.get('source_import'): raise HTTPException(409,'导入标识冲突，请重新预览。')
        return {'run_id':identifier,'status':existing['status'],'executed':False,'replayed':True,'message':'此预览已导入，复用已有项目。'}
    try:
        runner.initialize_workspace(identifier)
        runner.apply_generated_files(identifier, [{'path': name, 'content': text} for name, text in value['files'].items()], replace_all=True)
        commit = project._snapshot(identifier, 'source-import')
    except Exception as error:
        # Only this unpublished new UUID is eligible for rollback.
        import shutil
        for directory in (runner.workspace_for(identifier), runner.ROOT / 'data' / 'projects' / identifier, runner.ROOT / 'data' / 'git' / identifier):
            if directory.is_dir() and not directory.is_symlink(): shutil.rmtree(directory)
        raise HTTPException(409, '导入未完成，本次新建工作区已回退，原文件不受影响。') from error
    now = datetime.now(timezone.utc).isoformat()
    state = {'run_id': identifier, 'runtime_version': 2, 'status': 'paused', 'stage': 'source_import',
             'source_import': True, 'requirements_confirmed': False, 'can_resume': False,
             'user_request': payload.summary, 'product_spec': spec, 'acceptance_criteria': spec['acceptance_criteria'],
             'created_at': now, 'plan': [], 'repair_round': 0, 'run_budget_cny': 0, 'model_call_limit': 0,
             'import_report': public(value), 'trace': [{'stage': 'source_import', 'status': 'completed', 'at': now,
                'summary': '已复制用户主动上传的受控源码并建立Git基线；未执行，未验收。继续维护须另行确认产品方案。', 'commit': commit}]}
    try: store.create_run(identifier, payload.summary, state, now)
    except Exception as error:
        raise HTTPException(409,'源码已保留，但项目记录保存未完成；可用同一预览重试，未自动执行。') from error
    return {'run_id': identifier, 'status': 'paused', 'executed': False, 'message': '已导入独立暂停项目；通过“在此项目上继续修改”整理并确认后续方案。'}
