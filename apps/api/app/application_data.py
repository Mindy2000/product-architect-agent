"""Explicit application-data backups and restores. Source and Agent secrets are excluded."""
from contextlib import closing
import hashlib
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
import uuid
import zipfile
from . import runner,fullstack,project,store
from .app_file_store import FileStore,FileStoreError,MAX_TOTAL,MAX_FILE,MAX_FILES,TYPES
from .crud_schema import CrudSchema

ROOT=runner.ROOT/'data'/'application-backups'
MAX_ARCHIVE=512*1024*1024


class DataBackupError(Exception): pass


def directory(name):
    if (runner.ROOT/'data').is_symlink() or ROOT.is_symlink() or (ROOT/name).is_symlink():raise DataBackupError('数据备份目录异常。')
    path=ROOT/name;path.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path


def path_for(name,token):
    path=directory(name)/(str(uuid.UUID(str(token)))+'.zip')
    if path.is_symlink():raise DataBackupError('数据备份文件路径异常。')
    return path


def sha_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(256*1024):digest.update(chunk)
    return digest.hexdigest()


def readonly(path):
    if path.is_symlink() or not path.is_file():raise DataBackupError('数据库文件缺失或路径异常。')
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5);conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA trusted_schema=OFF')
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,16*1024*1024)
    return conn


def validate_database(root,schema):
    canonical=json.dumps(schema,ensure_ascii=False,sort_keys=True)
    digest=hashlib.sha256(canonical.encode()).hexdigest()
    expected={'pa_schema_history',*(entity['name'] for entity in schema['entities'])}
    with closing(readonly(root/'app.sqlite3')) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise DataBackupError('业务数据库完整性检查未通过。')
        objects=conn.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if any(row['type'] in {'view','trigger'} or re.match(r'\s*CREATE\s+VIRTUAL\s+TABLE',row['sql'] or '',re.I) for row in objects):raise DataBackupError('备份数据库含不支持的视图或触发器。')
        if {row['name'] for row in objects if row['type']=='table'}!=expected:raise DataBackupError('业务数据库表与当前结构不一致。')
        history=conn.execute('SELECT version,digest FROM pa_schema_history ORDER BY version DESC LIMIT 1').fetchone()
        if not history or history['version']!=schema['version'] or history['digest']!=digest:raise DataBackupError('数据库迁移版本与结构不一致。')
        for entity in schema['entities']:
            columns=conn.execute('PRAGMA table_info("'+entity['name']+'")').fetchall()
            if {row['name'] for row in columns}!={'id','created_at','updated_at',*(field['name'] for field in entity['fields'])}:raise DataBackupError('数据库字段与结构不一致。')
    index=root/'files.sqlite3';registered={}
    if index.exists():
        with closing(readonly(index)) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise DataBackupError('文件索引完整性检查未通过。')
            objects=conn.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if any(row['type'] in {'view','trigger'} for row in objects) or {row['name'] for row in objects if row['type']=='table'}!={'files'}:raise DataBackupError('文件索引结构不支持。')
            if {row['name'] for row in conn.execute('PRAGMA table_info(files)')}!={'id','name','mime','bytes','sha256','status','created_at'}:raise DataBackupError('文件索引字段不匹配。')
            rows=conn.execute('SELECT * FROM files LIMIT 501').fetchall()
            if len(rows)>MAX_FILES:raise DataBackupError('附件数量超限。')
            for row in rows:
                identifier=str(uuid.UUID(row['id']))
                if identifier!=row['id'] or identifier in registered:raise DataBackupError('附件标识重复或格式不规范。')
                if row['mime']!=TYPES.get(Path(row['name']).suffix.lower()):raise DataBackupError('附件类型与文件名不一致。')
                if row['status']!='ready':raise DataBackupError('存在未完成上传，请先显式整理临时文件。')
                if not 0<row['bytes']<=MAX_FILE or not re.fullmatch(r'[0-9a-f]{64}',row['sha256']):raise DataBackupError('文件元信息无效。')
                if not 1<=len(row['name'])<=180 or re.search(r'[/\\\x00-\x1f\x7f]',row['name']) or Path(row['name']).suffix.lower() not in TYPES:raise DataBackupError('文件名或类型不符合当前支持范围。')
                path=root/'uploads'/(identifier+'.blob')
                if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['bytes'] or sha_file(path)!=row['sha256']:raise DataBackupError('附件内容与索引不一致。')
                registered[identifier]=row['bytes']
    if sum(registered.values())>MAX_TOTAL:raise DataBackupError('附件总量超过256MiB。')
    uploads=root/'uploads'
    if uploads.is_symlink():raise DataBackupError('附件目录异常。')
    if uploads.exists():
        actual={file.name for file in uploads.iterdir() if file.is_file() and not file.is_symlink()}
        if actual!={identifier+'.blob' for identifier in registered}:raise DataBackupError('存在未索引附件或临时文件，请先整理后备份。')
        if any(file.is_symlink() or not file.is_file() for file in uploads.iterdir()):raise DataBackupError('附件目录含不支持条目。')
    return registered


def export_backup(run_id):
    state=store.get_run(run_id) or {};source=project.sources(run_id)
    if fullstack.application_type(run_id)!='fullstack_crud':raise DataBackupError('仅全栈本地应用具有独立业务数据库。')
    schema=CrudSchema.model_validate_json(source['backend/schema.json']).model_dump()
    root=fullstack.data_dir(run_id)
    if not (root/'app.sqlite3').is_file():raise DataBackupError('数据库尚未初始化。')
    registered=validate_database(root,schema)
    candidates={'database/app.sqlite3':root/'app.sqlite3'}
    if (root/'files.sqlite3').exists():candidates['database/files.sqlite3']=root/'files.sqlite3'
    candidates.update({'uploads/'+identifier+'.blob':root/'uploads'/(identifier+'.blob') for identifier in registered})
    if any(path.stat().st_size>(16*1024*1024 if name=='database/files.sqlite3' else 128*1024*1024) for name,path in candidates.items()) or sum(path.stat().st_size for path in candidates.values())>MAX_ARCHIVE:raise DataBackupError('数据备份超过保护上限。')
    exports=directory('exports')
    if any(path.is_symlink() for path in exports.glob('*.zip')):raise DataBackupError('备份目录含异常路径。')
    if len(list(exports.glob('*.zip')))>=20 or sum(path.stat().st_size for path in exports.glob('*.zip'))+sum(path.stat().st_size for path in candidates.values())>2*1024*1024*1024:raise DataBackupError('备份达到20份或2GiB上限，请先人工整理。')
    token=str(uuid.uuid4());target=path_for('exports',token)
    schema_bytes=json.dumps(schema,ensure_ascii=False,sort_keys=True).encode()
    manifest={'format':'pa-application-data','version':1,'source_run_id':run_id,'source_digest':project.manifest(run_id)['digest'],'created_at':time.time(),'schema_sha256':hashlib.sha256(schema_bytes).hexdigest(),'files':[]}
    try:
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            for name,path in candidates.items():
                manifest['files'].append({'path':name,'bytes':path.stat().st_size,'sha256':sha_file(path)})
                archive.write(path,name)
            archive.writestr('schema.json',schema_bytes)
            manifest['files'].append({'path':'schema.json','bytes':len(schema_bytes),'sha256':hashlib.sha256(schema_bytes).hexdigest()})
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
        target.chmod(0o600)
    except Exception:target.unlink(missing_ok=True);raise
    return token,target


def inspect_backup(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_ARCHIVE:raise DataBackupError('数据备份文件无效或超过512MiB。')
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist();names={info.filename for info in infos}
        if len(infos)>MAX_FILES+4 or len(names)!=len(infos) or sum(info.file_size for info in infos)>MAX_ARCHIVE:raise DataBackupError('备份条目重复、数量或展开大小超限。')
        for info in infos:
            allowed=info.filename in {'manifest.json','schema.json','database/app.sqlite3','database/files.sqlite3'} or re.fullmatch(r'uploads/[0-9a-f-]{36}\.blob',info.filename)
            if not allowed or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16) or info.is_dir():raise DataBackupError('备份含不允许的条目。')
            limit=256*1024 if info.filename=='manifest.json' else 65536 if info.filename=='schema.json' else 128*1024*1024 if info.filename=='database/app.sqlite3' else 16*1024*1024 if info.filename=='database/files.sqlite3' else MAX_FILE
            if info.file_size>limit:raise DataBackupError('备份单项超限。')
            if info.filename.startswith('uploads/') and str(uuid.UUID(Path(info.filename).stem))!=Path(info.filename).stem:raise DataBackupError('附件标识不规范。')
        if not {'manifest.json','schema.json','database/app.sqlite3'}<=names:raise DataBackupError('备份缺少必要文件。')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!='pa-application-data' or manifest.get('version')!=1:raise DataBackupError('备份格式不支持。')
        entries={entry['path']:entry for entry in manifest['files']}
        if len(entries)!=len(manifest['files']) or set(entries)|{'manifest.json'}!=names:raise DataBackupError('清单与备份内容不一致。')
        for info in infos:
            if info.filename=='manifest.json':continue
            entry=entries[info.filename];digest=hashlib.sha256()
            with archive.open(info) as stream:
                while chunk:=stream.read(256*1024):digest.update(chunk)
            if info.file_size!=entry['bytes'] or digest.hexdigest()!=entry['sha256']:raise DataBackupError('文件哈希或大小校验失败。')
        schema=CrudSchema.model_validate_json(archive.read('schema.json')).model_dump()
        canonical=json.dumps(schema,ensure_ascii=False,sort_keys=True).encode()
        if hashlib.sha256(canonical).hexdigest()!=manifest['schema_sha256']:raise DataBackupError('数据结构校验失败。')
        return {'schema':schema,'manifest':manifest,'attachment_count':sum(name.startswith('uploads/') for name in names),'uncompressed_bytes':sum(info.file_size for info in infos),'database_validation':'restore_before_publish','expires_seconds':86400}


def restore(token,source_run_id):
    archive_path=path_for('previews',token)
    if not archive_path.is_file() or time.time()-archive_path.stat().st_mtime>86400:raise DataBackupError('预览已过期，请重新上传。')
    report=inspect_backup(archive_path);parent=store.get_run(source_run_id)
    if not parent or fullstack.application_type(source_run_id)!='fullstack_crud':raise DataBackupError('请选择对应的本地全栈源码项目。')
    source=project.sources(source_run_id)
    schema=CrudSchema.model_validate_json(source['backend/schema.json']).model_dump()
    if schema!=report['schema']:raise DataBackupError('当前源码的数据结构与备份不一致，请先恢复对应源码版本。')
    identifier=str(uuid.UUID(str(token)));existing=store.get_run(identifier)
    if existing:
        if existing.get('data_restore_token')!=identifier:raise DataBackupError('恢复标识冲突。')
        return {'run_id':identifier,'status':existing['status'],'replayed':True}
    target=fullstack.data_dir(identifier)
    if any(target.iterdir()):raise DataBackupError('恢复目录已有内容，未覆盖，请重新预览。')
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                name=info.filename
                if name in {'manifest.json','schema.json'}:continue
                if name.startswith('uploads/'):
                    identifier_part=str(uuid.UUID(Path(name).stem));destination=target/'uploads'/(identifier_part+'.blob')
                    destination.parent.mkdir(exist_ok=True,mode=0o777);destination.parent.chmod(0o777)
                else:destination=target/Path(name).name
                with archive.open(info) as source_stream,destination.open('xb') as dest:shutil.copyfileobj(source_stream,dest,256*1024)
                destination.chmod(0o666)
        validate_database(target,schema)
        spec=dict(parent['product_spec']);spec['crud_schema']=schema
        runner.initialize_workspace(identifier,source_run_id,application_type='fullstack_crud',crud_schema=schema)
        now=datetime.now(timezone.utc).isoformat()
        state={'run_id':identifier,'parent_run_id':source_run_id,'runtime_version':2,'status':'paused','stage':'data_restore','data_restore_token':identifier,'local_data_restored':True,
               'product_spec':spec,'acceptance_criteria':spec['acceptance_criteria'],'plan':[],'repair_round':0,'requirements_confirmed':True,
               'user_request':'从本地业务数据备份创建独立副本','created_at':now,'can_resume':False,'run_budget_cny':0,'model_call_limit':0,
               'trace':[{'stage':'data_restore','status':'completed','at':now,'summary':'已校验并复制数据库与附件到独立项目；原数据保留，未启动应用。'}]}
        project.atomic_json(target/'migration.json',{'restored':True,'version':schema['version'],'attachment_count':report['attachment_count']})
        store.create_run(identifier,state['user_request'],state,now)
        return {'run_id':identifier,'status':'paused','executed':False}
    except Exception:
        if not store.get_run(identifier):
            for directory_path in (target,runner.workspace_for(identifier),runner.ROOT/'data/projects'/identifier,runner.ROOT/'data/git'/identifier):
                if directory_path.is_dir() and not directory_path.is_symlink():shutil.rmtree(directory_path)
        raise
