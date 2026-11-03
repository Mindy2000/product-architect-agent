"""Local attachment routes; no external transfer, OCR or model calls."""
import os
from pathlib import Path
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from backend.file_store import FileStore,FileStoreError,MAX_FILE

router=APIRouter()

def storage():
    return FileStore(Path('/tmp/pa-test-files') if os.environ.get('APP_DATABASE')=='/tmp/pa-test.sqlite3' else Path('/appdata'))

@router.get('/api/files')
def listing(offset:int=0,limit:int=50):
    try:return storage().list(offset,limit)
    except FileStoreError as error:raise HTTPException(409,str(error)) from None

@router.get('/api/storage')
def stats():return storage().stats()

@router.post('/api/files',status_code=201)
async def upload(request:Request,filename:str):
    store=storage();identifier=None
    try:
        identifier=store.begin(filename);size=0
        with store.path(identifier,'.part').open('xb') as stream:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_FILE:raise FileStoreError('文件超过20MiB。')
                stream.write(chunk)
        return store.finish(identifier)
    except BaseException as error:
        if identifier:store.abort(identifier)
        if isinstance(error,FileStoreError):raise HTTPException(422,str(error)) from None
        raise

@router.get('/api/files/{identifier}/download')
def download(identifier:str):
    try:
        path,row=storage().download(identifier)
        return FileResponse(path,filename=row['name'],media_type='application/octet-stream',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
    except (FileStoreError,ValueError) as error:raise HTTPException(404,'资料不存在或校验失败。') from None

@router.delete('/api/files/{identifier}',status_code=204)
def delete(identifier:str):
    try:storage().delete(identifier)
    except (FileStoreError,ValueError) as error:raise HTTPException(409,str(error)) from None

@router.get('/api/storage/cleanup')
def cleanup_preview():return storage().cleanup_preview()

@router.post('/api/storage/cleanup')
def cleanup():return storage().cleanup()
