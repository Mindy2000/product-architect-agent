"""Generated application's trusted FastAPI runtime. Run in the isolated worker only."""
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from backend.schema_contract import CrudSchema

SCHEMA = CrudSchema.model_validate_json((Path(__file__).parent/'schema.json').read_text())
DB = Path(os.environ.get('APP_DATABASE', '/appdata/app.sqlite3'))
if str(DB) not in {'/appdata/app.sqlite3','/tmp/pa-test.sqlite3'}: raise RuntimeError('固定数据库路径不匹配')
TYPES={'text':'TEXT','integer':'INTEGER','number':'REAL','boolean':'INTEGER'}
VALUE=SCHEMA.model_dump(); CANON=json.dumps(VALUE,ensure_ascii=False,sort_keys=True); DIGEST=hashlib.sha256(CANON.encode()).hexdigest()
ENTITIES={entity.name:entity for entity in SCHEMA.entities}


def connect():
    if DB.is_symlink(): raise RuntimeError('数据库路径异常')
    conn=sqlite3.connect(DB,timeout=5); conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA max_page_count=32768')  # 128MiB at the default 4096-byte page size.
    return conn


def state(conn):
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pa_schema_history'").fetchone()
    return conn.execute('SELECT * FROM pa_schema_history ORDER BY version DESC LIMIT 1').fetchone() if exists else None


def migration_plan(previous):
    if previous is None:
        if SCHEMA.version!=1 and str(DB)!='/tmp/pa-test.sqlite3': raise ValueError('新数据库必须从版本1开始')
        return [('create',entity,None) for entity in SCHEMA.entities]
    if previous['digest']==DIGEST: return []
    if SCHEMA.version!=previous['version']+1: raise ValueError('迁移版本必须依次递增，不能跳过或降级')
    old=CrudSchema.model_validate_json(previous['schema_json'])
    before={entity.name:entity for entity in old.entities}
    if before.keys()-ENTITIES.keys(): raise ValueError('禁止自动删除数据表')
    steps=[]
    for entity in SCHEMA.entities:
        if entity.name not in before: steps.append(('create',entity,None)); continue
        original={field.name:field for field in before[entity.name].fields}
        current={field.name:field for field in entity.fields}
        if original.keys()-current.keys(): raise ValueError('禁止自动删除字段')
        for name,field in current.items():
            if name not in original:
                if field.required: raise ValueError('已有表只能新增可空字段')
                steps.append(('add',entity,field))
            elif field.type!=original[name].type or field.required!=original[name].required or field.max_length<original[name].max_length:
                raise ValueError('禁止自动改变字段类型、可空性或缩小长度')
    return steps


def migrate():
    DB.parent.mkdir(parents=True,exist_ok=True)
    with closing(connect()) as conn:
        conn.execute('PRAGMA journal_mode=DELETE')
        previous=state(conn)
        steps=migration_plan(previous)
        if previous and previous['digest']==DIGEST: return {'changed':False,'version':SCHEMA.version}
        backup=None
        if previous:
            directory=DB.parent/'backups'; directory.mkdir(exist_ok=True)
            if len(list(directory.glob('*.sqlite3')))>=20: raise ValueError('迁移备份达到20份，请先人工整理')
            if sum(path.stat().st_size for path in directory.glob('*.sqlite3'))+DB.stat().st_size>256*1024*1024:raise ValueError('迁移备份合计达到256MiB上限')
            backup=directory/f'pre-v{SCHEMA.version}-{uuid.uuid4().hex}.sqlite3'
            with sqlite3.connect(backup) as destination: conn.backup(destination)
        conn.execute('BEGIN IMMEDIATE')
        try:
            # Re-check under lock to detect concurrent migrations before applying any DDL.
            current=state(conn)
            if (current['digest'] if current else None)!=(previous['digest'] if previous else None): raise ValueError('迁移基线已变化')
            conn.execute('CREATE TABLE IF NOT EXISTS pa_schema_history(version INTEGER PRIMARY KEY,digest TEXT NOT NULL,schema_json TEXT NOT NULL,applied_at TEXT NOT NULL)')
            for operation,entity,field in steps:
                if operation=='create':
                    fields=', '.join(f'"{f.name}" {TYPES[f.type]}'+(' NOT NULL' if f.required else '') for f in entity.fields)
                    conn.execute(f'CREATE TABLE "{entity.name}" (id TEXT PRIMARY KEY,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,{fields})')
                else: conn.execute(f'ALTER TABLE "{entity.name}" ADD COLUMN "{field.name}" {TYPES[field.type]}')
            conn.execute('INSERT INTO pa_schema_history VALUES (?,?,?,?)',(SCHEMA.version,DIGEST,CANON,datetime.now(timezone.utc).isoformat()))
            conn.commit()
        except Exception: conn.rollback(); raise
    return {'changed':True,'version':SCHEMA.version,'backup':backup.name if backup else None,'operations':len(steps)}


@asynccontextmanager
async def lifespan(app):
    if not DB.is_file(): raise RuntimeError('项目数据库未初始化，请先显式执行迁移')
    with closing(connect()) as conn:
        previous=state(conn)
        if not previous or previous['digest']!=DIGEST: raise RuntimeError('数据库版本与源码不一致，请先显式执行迁移')
    yield


app=FastAPI(title='Generated CRUD API',lifespan=lifespan,docs_url=None,redoc_url=None)
from backend.files_runtime import router as file_router
app.include_router(file_router)


@app.middleware('http')
async def guard(request,call_next):
    limit=20*1024*1024 if request.url.path=='/api/files' and request.method=='POST' else 65536
    if request.headers.get('content-length') and int(request.headers['content-length'])>limit:
        return JSONResponse(status_code=413,content={'detail':'请求正文超过本接口上限'})
    if request.method in {'POST','PATCH','DELETE'}:
        origin=request.headers.get('origin')
        if origin and origin not in {'http://127.0.0.1:4173','http://localhost:4173'}:
            return JSONResponse(status_code=403,content={'detail':'只接受本应用同源请求'})
    return await call_next(request)


def entity_or_404(name):
    if name not in ENTITIES: raise HTTPException(404,'未知数据表')
    return ENTITIES[name]


def validate(entity,body,partial=False):
    if not isinstance(body,dict): raise HTTPException(422,'请求须为JSON对象')
    fields={f.name:f for f in entity.fields}
    if body.keys()-fields.keys(): raise HTTPException(422,'包含未知或保留字段')
    for field in entity.fields:
        if field.name not in body:
            if field.required and not partial: raise HTTPException(422,'缺少必填字段：'+field.name)
            continue
        value=body[field.name]
        if value is None:
            if field.required: raise HTTPException(422,'必填字段不能为空')
            continue
        okay=(field.type=='text' and isinstance(value,str) and len(value)<=field.max_length
              or field.type=='integer' and type(value) is int and -(2**53)<value<2**53
              or field.type=='number' and type(value) in {int,float} and math.isfinite(value) and abs(value)<1e15
              or field.type=='boolean' and type(value) is bool)
        if not okay: raise HTTPException(422,'字段类型或长度不符：'+field.name)
    return body


def output(entity,row):
    value=dict(row)
    for field in entity.fields:
        if field.type=='boolean' and value.get(field.name) is not None:value[field.name]=bool(value[field.name])
    return value


async def body(request):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>65536: raise HTTPException(413,'正文超限')
    try: return json.loads(raw)
    except (ValueError,UnicodeError):raise HTTPException(422,'JSON不完整')


@app.get('/api/health')
def health(): return {'status':'ok','schema_version':SCHEMA.version,'schema_sha256':DIGEST}


@app.get('/api/schema')
def schema(): return VALUE


@app.get('/api/data/{name}')
def listing(name:str,limit:int=50,offset:int=0):
    entity=entity_or_404(name)
    if not 1<=limit<=100 or not 0<=offset<=100000:raise HTTPException(422,'分页超限')
    with closing(connect()) as conn:
        rows=conn.execute(f'SELECT * FROM "{name}" ORDER BY created_at,id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
        total=conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    return {'items':[output(entity,row) for row in rows],'total':total}


@app.get('/api/data/{name}/{identifier}')
def read(name:str,identifier:str):
    entity=entity_or_404(name)
    with closing(connect()) as conn:row=conn.execute(f'SELECT * FROM "{name}" WHERE id=?',(identifier,)).fetchone()
    if row is None:raise HTTPException(404,'记录不存在')
    return output(entity,row)


@app.post('/api/data/{name}',status_code=201)
async def create(name:str,request:Request):
    entity=entity_or_404(name); values=validate(entity,await body(request)); now=datetime.now(timezone.utc).isoformat()
    values={'id':str(uuid.uuid4()),'created_at':now,'updated_at':now,**values}
    columns=','.join('"'+name+'"' for name in values); marks=','.join('?' for _ in values)
    with closing(connect()) as conn,conn:
        if conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]>=10000:raise HTTPException(409,'单表达到10000条记录上限')
        conn.execute(f'INSERT INTO "{name}" ({columns}) VALUES ({marks})',tuple(values.values()))
    return read(name,values['id'])


@app.patch('/api/data/{name}/{identifier}')
async def update(name:str,identifier:str,request:Request):
    entity=entity_or_404(name); values=validate(entity,await body(request),partial=True)
    if not values:raise HTTPException(422,'没有待修改字段')
    values['updated_at']=datetime.now(timezone.utc).isoformat()
    assignments=','.join('"'+key+'"=?' for key in values)
    with closing(connect()) as conn,conn:
        changed=conn.execute(f'UPDATE "{name}" SET {assignments} WHERE id=?',(*values.values(),identifier)).rowcount
        if not changed:raise HTTPException(404,'记录不存在')
    return read(name,identifier)


@app.delete('/api/data/{name}/{identifier}',status_code=204)
def delete(name:str,identifier:str):
    entity_or_404(name)
    with closing(connect()) as conn,conn:
        if not conn.execute(f'DELETE FROM "{name}" WHERE id=?',(identifier,)).rowcount:raise HTTPException(404,'记录不存在')


if __name__=='__main__':
    if sys.argv[1:]!=['migrate']:raise SystemExit('只允许固定迁移命令')
    print(json.dumps(migrate(),ensure_ascii=False))
