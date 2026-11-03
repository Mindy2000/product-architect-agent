"""Workbench controls for local app files and data copies; no account system."""
import asyncio
from contextlib import ExitStack
import json
import sqlite3
from pathlib import Path
import time
from uuid import UUID,uuid4
import zipfile
from fastapi import APIRouter,HTTPException,Request,Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict
from . import fullstack,project,store,runner,preview
from .app_file_store import FileStore,FileStoreError,MAX_FILE
from . import application_data as backups

async def local_mutation(request:Request):
    origin=request.headers.get('origin')
    allowed={'http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000'}
    if request.method in {'POST','PUT','DELETE'} and origin and origin not in allowed:raise HTTPException(403,'本地数据操作只接受本机工作台来源。')


router=APIRouter(prefix='/api/v1',dependencies=[Depends(local_mutation)])
upload_slots=asyncio.Semaphore(2)
backup_upload_slot=asyncio.Semaphore(1)


def require(identifier,quiet=False):
    state=store.get_run(identifier)
    if not state or fullstack.application_type(identifier)!='fullstack_crud':raise HTTPException(409,'请选择已建立工作区的本地全栈应用。')
    if quiet:
        if state.get('status') not in {'completed','paused','failed_validation','failed','failed_sandbox','interrupted','cancelled','failed_model','blocked_budget','blocked_configuration'}:raise HTTPException(409,'请等待项目开发结束后管理业务数据。')
        preview.close_preview(identifier)
    return FileStore(fullstack.data_dir(identifier))


@router.get('/projects/{run_id}/files')
def list_files(run_id:UUID,offset:int=0,limit:int=50):
    try:return require(str(run_id)).list(offset,limit)
    except (FileStoreError,ValueError) as error:raise HTTPException(409,str(error)) from None


@router.get('/projects/{run_id}/storage')
def storage(run_id:UUID):
    try:return require(str(run_id)).stats()
    except FileStoreError as error:raise HTTPException(409,str(error)) from None


@router.post('/projects/{run_id}/files',status_code=201)
async def upload(run_id:UUID,request:Request,filename:str):
    identifier=str(run_id)
    async with upload_slots:
        with project.project_lock(identifier):
            file_store=require(identifier,True);file_id=None
            try:
                file_id=file_store.begin(filename);size=0
                with file_store.path(file_id,'.part').open('xb') as stream:
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>MAX_FILE:raise FileStoreError('文件超过20MiB。')
                        stream.write(chunk)
                return file_store.finish(file_id)
            except BaseException as error:
                if file_id:file_store.abort(file_id)
                if isinstance(error,FileStoreError):raise HTTPException(422,str(error)) from None
                raise


@router.get('/projects/{run_id}/files/{file_id}/download')
def download(run_id:UUID,file_id:UUID):
    try:
        path,row=require(str(run_id)).download(str(file_id))
        return FileResponse(path,filename=row['name'],media_type='application/octet-stream',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
    except FileStoreError as error:raise HTTPException(409,str(error)) from None


@router.delete('/projects/{run_id}/files/{file_id}',status_code=204)
def delete(run_id:UUID,file_id:UUID):
    with project.project_lock(str(run_id)):
        try:require(str(run_id),True).delete(str(file_id))
        except FileStoreError as error:raise HTTPException(409,str(error)) from None


@router.get('/projects/{run_id}/storage/cleanup')
def cleanup_preview(run_id:UUID):return require(str(run_id)).cleanup_preview()


@router.post('/projects/{run_id}/storage/cleanup')
def cleanup(run_id:UUID):
    with project.project_lock(str(run_id)):return require(str(run_id),True).cleanup()


@router.post('/projects/{run_id}/data-backups')
def create_backup(run_id:UUID):
    with project.project_lock(str(run_id)):
        require(str(run_id),True)
        try:
            token,path=backups.export_backup(str(run_id))
            return {'token':token,'bytes':path.stat().st_size,'download_url':f'/api/v1/projects/{run_id}/data-backups/{token}/download'}
        except (backups.DataBackupError,FileStoreError,ValueError,TypeError,sqlite3.DatabaseError,zipfile.BadZipFile) as error:raise HTTPException(409,str(error)) from None


@router.get('/projects/{run_id}/data-backups/{token}/download')
def download_backup(run_id:UUID,token:UUID):
    require(str(run_id))
    try:
        path=backups.path_for('exports',token)
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo('manifest.json').file_size>256*1024:raise ValueError()
            if json.loads(archive.read('manifest.json'))['source_run_id']!=str(run_id):raise ValueError()
        return FileResponse(path,filename=f'{run_id}-application-data.zip',media_type='application/zip',headers={'Cache-Control':'no-store'})
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):raise HTTPException(404,'备份不存在或不属于当前项目。') from None


@router.post('/application-data/preview')
async def preview_backup(request:Request):
    async with backup_upload_slot:
        directory=backups.directory('previews')
        existing=list(directory.glob('*.zip'))
        if any(path.is_symlink() for path in existing):raise HTTPException(409,'备份暂存目录含异常路径，已停止。')
        if len(existing)>=4 or sum(path.stat().st_size for path in existing)+backups.MAX_ARCHIVE>2*1024*1024*1024:raise HTTPException(409,'预览暂存达到上限，请先整理过期预览。')
        token=str(uuid4());path=backups.path_for('previews',token)
        try:
            size=0
            with path.open('xb') as stream:
                path.chmod(0o600)
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>backups.MAX_ARCHIVE:raise backups.DataBackupError('数据备份不能超过512MiB。')
                    stream.write(chunk)
            report=await asyncio.to_thread(backups.inspect_backup,path)
            return {'token':token,**report}
        except BaseException as error:
            path.unlink(missing_ok=True)
            if isinstance(error,(backups.DataBackupError,ValueError,KeyError,TypeError,zipfile.BadZipFile)):raise HTTPException(422,'备份格式或文件清单校验未通过；不会导入或执行内容。') from None
            raise


class RestoreData(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:UUID
    source_run_id:UUID


@router.post('/application-data/restore')
def restore_backup(payload:RestoreData):
    with ExitStack() as stack:
        for identifier in sorted({str(payload.source_run_id),str(payload.token)}):stack.enter_context(project.project_lock(identifier))
        require(str(payload.source_run_id),True)
        try:return backups.restore(str(payload.token),str(payload.source_run_id))
        except (backups.DataBackupError,FileStoreError,ValueError,KeyError,TypeError,sqlite3.DatabaseError,zipfile.BadZipFile) as error:raise HTTPException(409,'恢复未完成，原数据保留；'+str(error)[:400]) from None


@router.get('/application-data/cleanup')
def data_cleanup_preview():
    items=[]
    for path in backups.directory('previews').glob('*.zip'):
        if path.is_symlink() or time.time()-path.stat().st_mtime<=86400:continue
        try:token=str(UUID(path.stem))
        except ValueError:continue
        items.append({'token':token,'bytes':path.stat().st_size})
    return {'items':items[:100],'scope':'expired_preview_only','minimum_age_hours':24}


@router.post('/application-data/cleanup')
def data_cleanup():
    plan=data_cleanup_preview()
    for item in plan['items']:backups.path_for('previews',item['token']).unlink(missing_ok=True)
    return {'removed_entries':len(plan['items'])}
