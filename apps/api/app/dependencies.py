"""Locked dependency inventory and durable requests; never run arbitrary npm commands."""
import json
from contextlib import closing
import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import runner, store

router = APIRouter(prefix='/api/v1/dependencies')
# Build tools are not application imports. Subpath imports are explicit, not prefix matches.
APP_IMPORTS = {'react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/client', 'lucide-react', 'tailwindcss'}
TEST_IMPORTS = {'@playwright/test'}


def inventory():
    package = json.loads((runner.WORKER_CONTEXT / 'package.json').read_text())
    lock = json.loads((runner.WORKER_CONTEXT / 'package-lock.json').read_text())
    result = []
    for name, version in sorted(package['dependencies'].items()):
        entry = lock.get('packages', {}).get('node_modules/' + name, {})
        if entry.get('version') != version or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
            raise runner.SandboxError('固定依赖与锁文件不一致，已停止依赖操作。')
        result.append({'name': name, 'version': version, 'integrity': entry.get('integrity', ''),
                       'role': 'application' if name in APP_IMPORTS else 'test' if name in TEST_IMPORTS else 'toolchain'})
    return result


def policy():
    return {'image': runner.WORKER_IMAGE, 'installation': 'fixed_image_only', 'runtime_network': False,
            'automatic': 'reuse_locked_allowlist', 'outside_allowlist': 'operator_review_required',
            'application_imports': sorted(APP_IMPORTS), 'test_imports': sorted(TEST_IMPORTS), 'packages': inventory()}


class DependencyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$', max_length=120)
    version: str = Field(default='', max_length=60, pattern=r'^[0-9A-Za-z.*^~<>=| +_-]*$')
    reason: str = Field(min_length=1, max_length=600)


def connect():
    connection = store.connect()
    connection.execute('CREATE TABLE IF NOT EXISTS dependency_requests(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,name TEXT NOT NULL,version TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,locked_version TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,name,version))')
    connection.commit()
    return connection


def request_dependency(run_id, payload):
    if not store.get_run(run_id): raise HTTPException(404, '项目不存在。')
    available = {item['name']: item for item in inventory()}
    package = available.get(payload.name)
    # Exact / unspecified versions may reuse the known image; ranges are not guessed compatible.
    status = 'available_locked' if package and payload.version in {'', package['version']} else 'review_required'
    with closing(connect()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        exists=connection.execute('SELECT 1 FROM dependency_requests WHERE run_id=? AND name=? AND version=?',(run_id,payload.name,payload.version)).fetchone()
        if not exists and (connection.execute('SELECT COUNT(*) FROM dependency_requests WHERE run_id=?',(run_id,)).fetchone()[0]>=100 or connection.execute('SELECT COUNT(*) FROM dependency_requests').fetchone()[0]>=2000):
            raise HTTPException(409,'依赖申请记录达到上限，请先整理现有申请。')
        connection.execute('INSERT OR IGNORE INTO dependency_requests VALUES (?,?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), run_id, payload.name, payload.version, payload.reason, status,
                            package['version'] if package else '', datetime.now(timezone.utc).isoformat()))
        row = connection.execute('SELECT * FROM dependency_requests WHERE run_id=? AND name=? AND version=?',
                                 (run_id, payload.name, payload.version)).fetchone()
    return {**dict(row), 'installed_now': False, 'message': '复用固定镜像中的依赖，不额外安装。' if status == 'available_locked' else '已登记兼容性与依赖审查申请；未安装，不可绕过白名单。'}


@router.get('/policy')
def read_policy(): return policy()


@router.get('/projects/{run_id}')
def list_requests(run_id: uuid.UUID):
    if not store.get_run(str(run_id)): raise HTTPException(404, '项目不存在。')
    connection = connect()
    try: return {'items': [dict(row) for row in connection.execute('SELECT * FROM dependency_requests WHERE run_id=? ORDER BY created_at DESC LIMIT 100', (str(run_id),))]}
    finally: connection.close()


@router.post('/projects/{run_id}')
def submit_request(run_id: uuid.UUID, payload: DependencyRequest):
    return request_dependency(str(run_id), payload)
