"""Trusted full-stack scaffolds and fixed container commands; never run app Python on host."""
import json
from contextlib import ExitStack
import os
import subprocess
from .process_control import run_bounded, OutputLimitExceeded
from .security_policy import redact, check_project_roots
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, HTTPException
from . import runner, store
from .crud_schema import CrudSchema

IMAGE='product-architect-fullstack:1.0'
router=APIRouter(prefix='/api/v1/projects')


def application_type(run_id):
    path=runner.ROOT/'data'/'projects'/str(UUID(str(run_id)))/'runtime.json'
    if path.is_symlink():raise runner.SandboxError('运行配置路径异常。')
    if not path.exists():return 'frontend'
    value=json.loads(path.read_text())
    if value.get('application_type') not in {'frontend','fullstack_crud'}:raise runner.SandboxError('未知应用类型。')
    return value['application_type']


def files(run_id):
    if application_type(run_id)!='fullstack_crud':return {}
    root=runner.ROOT
    proxy="import {defineConfig} from 'vite'\nimport react from '@vitejs/plugin-react'\nimport tailwindcss from '@tailwindcss/vite'\nconst proxy={'/api':{target:'http://127.0.0.1:8001',changeOrigin:false}}\nexport default defineConfig({plugins:[react(),tailwindcss()],server:{proxy},preview:{proxy}})\n"
    result={'BACKEND_README.md':'# 业务后端源码\n\n本工程使用固定FastAPI/SQLite CRUD运行时，模型只维护schema.json与前端源码。业务数据和密钥不包含在源码包中。运行须使用Product Architect的product-architect-fullstack:1.0隔离镜像（由runtime/fullstack构建），不会在宿主机执行Python。首次生产库初始化或升级须在项目面板显式操作；测试使用临时库。SQLite业务数据按项目UUID另存，不随源码恢复覆盖。\n',
            'backend/copy_data.py':(root/'templates/fullstack/copy_data.py').read_text(),
            'backend/file_store.py':(root/'apps/api/app/app_file_store.py').read_text(),
            'backend/files_runtime.py':(root/'templates/fullstack/files_runtime.py').read_text(),
            'backend/runtime.py':(root/'templates/fullstack/runtime.py').read_text(),
            'backend/schema_contract.py':(root/'apps/api/app/crud_schema.py').read_text(),
            'backend/wait_ready.py':(root/'templates/fullstack/wait_ready.py').read_text(),
            'backend/requirements.lock':(root/'runtime/fullstack/requirements.lock').read_text(),
            'src/pa-crud-client.ts':(root/'templates/fullstack/client.ts').read_text(),
            'vite.config.ts':proxy}
    manifest=runner.ROOT/'data/projects'/str(UUID(str(run_id)))/'manifest.json'
    if manifest.is_symlink():raise runner.SandboxError('框架清单路径异常。')
    if manifest.is_file():
        declared=json.loads(manifest.read_text()).get('framework',{})
        result={name:value for name,value in result.items() if name in declared}
    return result


def prepare(run_id,application_type_value,schema=None):
    from . import project
    if application_type_value not in {'frontend','fullstack_crud'}:raise runner.SandboxError('未知应用类型。')
    data={'application_type':application_type_value}
    if application_type_value=='fullstack_crud':data['schema']=CrudSchema.model_validate(schema).model_dump()
    path=project.metadata_dir(run_id)/'runtime.json'
    if path.exists():
        if json.loads(path.read_text()).get('application_type')!=application_type_value:raise runner.SandboxError('不能改变已有项目的运行类型。')
    else:project.atomic_json(path,data)
    return data


def data_dir(run_id):
    root=runner.ROOT/'data'/'applications'
    if (runner.ROOT/'data').is_symlink() or root.is_symlink():raise runner.SandboxError('业务数据目录异常。')
    path=root/str(UUID(str(run_id)))
    if path.is_symlink():raise runner.SandboxError('业务数据库路径异常。')
    path.mkdir(parents=True,exist_ok=True,mode=0o777);path.chmod(0o777)
    return path


def ensure_image(mode):
    if mode=='frontend':return runner.ensure_worker_image()
    result=run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'image','inspect',IMAGE],timeout=15)
    if result.returncode==0:return '业务后端固定镜像已缓存'
    # This function runs only after a new task is explicitly confirmed, never at API startup.
    runner.ensure_worker_image()
    result=run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'build','--tag',IMAGE,'--file',str(runner.ROOT/'runtime/fullstack/Dockerfile'),str(runner.ROOT/'runtime/fullstack')],timeout=900)
    if result.returncode:raise runner.SandboxError('业务后端镜像准备失败：'+result.stderr[-2000:])
    return '业务后端固定镜像已准备'


def fixed_script(phase):
    if phase=='build':return 'APP_DATABASE=/tmp/pa-test.sqlite3 /opt/fullstack/venv/bin/python -m backend.runtime migrate && npm run build'
    if phase=='migrate':return 'APP_DATABASE=/appdata/app.sqlite3 /opt/fullstack/venv/bin/python -m backend.runtime migrate'
    if phase not in {'test','acceptance','preview','regression','health'}:raise runner.SandboxError('未知全栈命令。')
    target={'health':'./node_modules/.bin/playwright test tests/standard/health.spec.ts --reporter=line','regression':'./node_modules/.bin/playwright test --reporter=line,json','test':'npm run test:e2e','acceptance':'./node_modules/.bin/playwright test tests/acceptance.spec.ts --reporter=line','preview':'./node_modules/.bin/playwright test tests/preview.spec.ts --reporter=line'}[phase]
    return 'set -e; export APP_DATABASE=/tmp/pa-test.sqlite3; /opt/fullstack/venv/bin/python -m backend.runtime migrate; /opt/fullstack/venv/bin/python -m uvicorn backend.runtime:app --host 127.0.0.1 --port 8001 --workers 1 >/tmp/backend.log 2>&1 & wait_backend=$!; trap "kill $wait_backend 2>/dev/null || true" EXIT; /opt/fullstack/venv/bin/python backend/wait_ready.py; '+target



def validate_contract(run_id,source):
    mode=application_type(run_id)
    schema=source.get('backend/schema.json')
    if mode=='frontend' and schema is not None:raise runner.SandboxError('前端项目不能自行启用业务后端。')
    if mode=='fullstack_crud':
        actual=CrudSchema.model_validate_json(schema or '').model_dump()
        state=store.get_run(run_id) or {}
        expected=(state.get('product_spec') or {}).get('crud_schema')
        if expected and actual!=expected:raise runner.SandboxError('数据结构必须与已确认方案一致；请先另建维护方案。')


@router.get('/{run_id}/backend')
def backend_status(run_id:UUID):
    identifier=str(run_id);state=store.get_run(identifier)
    if not state:raise HTTPException(404,'项目不存在。')
    if application_type(identifier)!='fullstack_crud':return {'enabled':False}
    from . import project
    source=project.sources(identifier);directory=data_dir(identifier)
    receipt=directory/'migration.json'
    return {'enabled':True,'runtime_image':IMAGE,'schema':json.loads(source['backend/schema.json']),
            'database_exists':(directory/'app.sqlite3').is_file(),
            'last_migration':json.loads(receipt.read_text()) if receipt.is_file() else None,
            'data_in_source_export':False,'migration_requires_explicit_action':True}


@router.post('/{run_id}/backend/migrate')
def migrate_database(run_id:UUID,inherit_parent:bool=False):
    identifier=str(run_id);state=store.get_run(identifier)
    if not state or application_type(identifier)!='fullstack_crud':raise HTTPException(409,'项目不是全栈CRUD应用。')
    if state.get('status') not in {'completed','failed_validation','paused','failed','failed_sandbox','interrupted'}:raise HTTPException(409,'请等待开发任务结束后迁移数据库。')
    from . import project,preview
    with ExitStack() as locks:
        for lock_id in sorted({identifier,*([state['parent_run_id']] if state.get('parent_run_id') else [])}):locks.enter_context(project.project_lock(lock_id))
        preview.close_preview(identifier)
        project.sources(identifier)
        directory=data_dir(identifier)
        parent_database=None
        if not (directory/'app.sqlite3').exists() and state.get('parent_run_id'):
            # Do not silently start a new empty DB when a parent has persistent data.
            parent=data_dir(state['parent_run_id'])/'app.sqlite3'
            if parent.is_file():
                if not inherit_parent:raise HTTPException(409,'父版本已有业务数据；请显式勾选复制父版本数据库，原库保留。')
                if parent.is_symlink():raise HTTPException(409,'父数据库路径异常。')
                preview.close_preview(state['parent_run_id'])
                parent_database=parent.parent
        result=runner.run_phase(identifier,'migrate',timeout=90,parent_database=parent_database)
        if result.exit_code:raise HTTPException(409,'迁移未完成，数据库事务回退；日志：'+result.output[-2000:])
        try:record=json.loads(result.output.strip())
        except ValueError:raise HTTPException(409,'迁移回执无法解析，原数据库保留，请检查运行记录。')
        project.atomic_json(directory/'migration.json',record)
        from datetime import datetime,timezone
        current=store.get_run(identifier) or state
        current['trace']=current.get('trace',[])+[{'stage':'database_migration','status':'completed','at':datetime.now(timezone.utc).isoformat(),'summary':'显式升级项目SQLite；原库保留，已禁止破坏性迁移','version':record.get('version'),'copied_parent':bool(parent_database)}]
        store.update_run(identifier,current)
        return record


@router.post('/{run_id}/backend/prepare-preview')
def prepare_local_preview(run_id:UUID):
    identifier=str(run_id);state=store.get_run(identifier)
    if not state or application_type(identifier)!='fullstack_crud' or state.get('status') not in {'completed','paused','failed_validation','failed','interrupted','failed_sandbox'}:raise HTTPException(409,'请等待开发任务结束。')
    from . import project,preview
    with project.project_lock(identifier):
        preview.close_preview(identifier);project.sources(identifier)
        result=runner.run_phase(identifier,'build',timeout=150)
        if result.exit_code:raise HTTPException(409,'本地预览构建未完成：'+result.output[-2000:])
        from datetime import datetime,timezone
        current=store.get_run(identifier) or state
        current['build_status']='passed'
        current['trace']=current.get('trace',[])+[{'stage':'local_preview_build','status':'completed','at':datetime.now(timezone.utc).isoformat(),'summary':'显式构建本地预览，未调用模型，业务数据未修改；不代表交互验收通过。'}]
        store.update_run(identifier,current)
        return {'passed':True,'model_calls':0,'message':'已构建当前源码，可显式打开项目预览；未修改业务数据库。'}
