"""Local-only safety policy. No users, passwords, sessions or remote access mode."""
from __future__ import annotations
from collections import deque
import ipaddress
import json
import logging
import os
from pathlib import Path,PurePosixPath
import re
import threading
import time
import traceback
from urllib.parse import quote
from uuid import uuid4
from dotenv import dotenv_values
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.responses import JSONResponse

ROOT=Path(__file__).resolve().parents[3]
ORIGINS={'http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000'}
HOSTS={'127.0.0.1','127.0.0.1:8000','localhost','localhost:8000','[::1]','[::1]:8000'}
OUTPUT_LIMIT=2*1024*1024
MAX_QUEUED_RUNS=8
_secret_lock=threading.RLock()
_secret_stamp=None
_secrets=[]


def protect_secret_file():
    path=ROOT/'.env'
    if path.is_symlink():raise RuntimeError('配置文件不允许使用符号链接。')
    if path.exists() and os.name=='posix':path.chmod(0o600)


def check_project_roots():
    paths=['data','outputs','generated-projects','data/projects','data/git','data/knowledge','data/applications','data/application-backups','data/research','data/vision','data/ocr']
    if any((ROOT/name).is_symlink() for name in paths):raise RuntimeError('受控数据目录存在符号链接，已停止自动访问。')


def redact(value):
    global _secret_stamp,_secrets
    text=str(value)
    with _secret_lock:
        path=ROOT/'.env'
        stamp=path.stat().st_mtime_ns if path.is_file() and not path.is_symlink() else None
        if stamp!=_secret_stamp or _secret_stamp is None:
            values=dict(os.environ)
            if stamp is not None:values.update(dotenv_values(path))
            found={str(v) for k,v in values.items() if v and len(str(v))>=8 and re.search(r'(?:API_KEY|ACCESS_KEY|SECRET|TOKEN|PASSWORD)$',k,re.I)}
            _secrets=sorted(found|{quote(v,safe='') for v in found},key=len,reverse=True);_secret_stamp=stamp
        for secret in _secrets:text=text.replace(secret,'[REDACTED]')
    text=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b','[REDACTED]',text)
    text=re.sub(r'(?i)(Bearer\s+)[^\s\x22\x27,}]+',r'\1[REDACTED]',text)
    text=re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@',r'\1[REDACTED]@',text)
    return text


class RedactFilter(logging.Filter):
    def filter(self,record):
        record.msg=redact(record.getMessage());record.args=()
        if record.exc_info:
            record.exc_text=redact(''.join(traceback.format_exception(*record.exc_info)));record.exc_info=None
        return True


def install_log_redaction():
    filter=RedactFilter()
    for logger in [logging.getLogger(),logging.getLogger('uvicorn'),logging.getLogger('uvicorn.error'),logging.getLogger('uvicorn.access')]:
        if not any(isinstance(item,RedactFilter) for item in logger.filters):logger.addFilter(filter)
        for handler in logger.handlers:
            if not any(isinstance(item,RedactFilter) for item in handler.filters):handler.addFilter(filter)


def body_limit(path):
    if path=='/api/v1/workbench/backups/preview':return 512*1024*1024
    if path=='/api/v1/application-data/preview':return 512*1024*1024
    if path=='/api/v1/recovery/preview':return 64*1024*1024
    if path=='/api/v1/projects/source-import/preview':return 16*1024*1024
    if re.fullmatch(r'/api/v1/(?:projects/[0-9a-f-]{36}/files|library/collections/[0-9a-f-]{36}/documents)',path):return 20*1024*1024
    if path in {'/api/v1/vision/artifacts','/api/v1/references'}:return 4*1024*1024
    return 1024*1024


def safe_output(rel):
    if not isinstance(rel,str) or '\\' in rel:raise ValueError('交付路径无效。')
    parts=PurePosixPath(rel).parts
    if not parts or parts[0]!='outputs' or '..' in parts or PurePosixPath(rel).is_absolute():raise ValueError('交付路径不在受控目录。')
    current=ROOT
    for part in parts:
        current=current/part
        if current.is_symlink():raise ValueError('交付路径不允许符号链接。')
    if not current.is_file():raise ValueError('交付文件不存在。')
    return current


class LocalSafetyMiddleware:
    def __init__(self,app):
        self.app=app;self.active=0;self.uploads=0;self.windows={};self.guards={}
    async def __call__(self,scope,receive,send):
        if scope['type']=='websocket':
            await send({'type':'websocket.close','code':1008});return
        if scope['type']!='http':return await self.app(scope,receive,send)
        identifier=uuid4().hex;scope.setdefault('state',{})['request_id']=identifier
        headers={key.lower():value for key,value in scope.get('headers',[])}
        async def reject(code,message):
            response=JSONResponse(status_code=code,content={'detail':message,'request_id':identifier},headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            await response(scope,receive,send)
        try:
            peer=ipaddress.ip_address((scope.get('client') or ('',0))[0])
            if getattr(peer,'ipv4_mapped',None):peer=peer.ipv4_mapped
            if not peer.is_loopback:return await reject(403,'此服务只允许本机访问。')
        except ValueError:return await reject(403,'无法确认本机连接。')
        host_values=[v for k,v in scope['headers'] if k.lower()==b'host']
        if len(host_values)!=1 or host_values[0].decode('ascii',errors='replace').lower() not in HOSTS:return await reject(400,'不接受该访问地址。')
        origin=headers.get(b'origin',b'').decode('ascii',errors='replace')
        if origin and origin not in ORIGINS:return await reject(403,'只允许本机工作台来源。')
        if not origin and headers.get(b'sec-fetch-site')==b'cross-site':return await reject(403,'拒绝其他网页发起的本机访问。')
        lengths=[v for k,v in scope['headers'] if k.lower()==b'content-length']
        if len(lengths)>1 or (lengths and (not lengths[0].isdigit() or len(lengths[0])>12)):return await reject(400,'请求长度格式无效。')
        if lengths and b'transfer-encoding' in headers:return await reject(400,'请求长度约定冲突。')
        limit=body_limit(scope['path'])
        if lengths and int(lengths[0])>limit:return await reject(413,'请求内容超过此操作的大小上限。')
        now=time.monotonic();method=scope['method'];path=scope['path']
        bucket=('preview' if '/preview/action' in path else 'write' if method in {'POST','PUT','PATCH','DELETE'} else 'read')
        quota={'preview':600,'write':120,'read':1200}[bucket]
        window=self.windows.setdefault(bucket,deque())
        while window and now-window[0]>=60:window.popleft()
        if len(window)>=quota:return await reject(429,'本机操作过于频繁，请稍后再试。')
        window.append(now)
        if self.active>=48:return await reject(503,'本机服务正忙，请稍后再试。')
        large=limit>4*1024*1024
        if large and self.uploads>=2:return await reject(503,'大文件操作并发已满，请稍后再试。')
        self.uploads+=int(large)
        self.active+=1;started=False;error_chunks=[];error_bytes=0;error_start=None
        async def guarded_send(message):
            nonlocal started,error_bytes,error_start
            if message['type']=='http.response.start':
                response_headers=list(message.get('headers',[]))
                response_headers=[(key,val) for key,val in response_headers if key.lower() not in {b'cache-control',b'server'}]
                response_headers.extend([(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff'),(b'referrer-policy',b'no-referrer'),(b'x-frame-options',b'DENY'),(b'permissions-policy',b'camera=(), microphone=(), geolocation=()'),(b'x-request-id',identifier.encode())])
                message={**message,'headers':response_headers}
                # Operational errors are bounded/redacted; successful user data remains exact.
                if message['status']>=400:error_start=message;return
                started=True
            if message['type']=='http.response.body' and error_start is not None:
                error_bytes+=len(message.get('body',b''))
                if error_bytes<=65536:error_chunks.append(message.get('body',b''))
                if message.get('more_body',False):return
                content=b''.join(error_chunks)
                try:
                    value=json.loads(content)
                    value['request_id']=identifier
                    body=redact(json.dumps(value,ensure_ascii=False)).encode()
                except (ValueError,TypeError):body=json.dumps({'detail':'操作未完成，请查看本机记录。','request_id':identifier},ensure_ascii=False).encode()
                error_start['headers']=[(k,v) for k,v in error_start['headers'] if k.lower() not in {b'content-length',b'content-type'}]+[(b'content-type',b'application/json; charset=utf-8'),(b'content-length',str(len(body)).encode())]
                await send(error_start);await send({'type':'http.response.body','body':body});started=True;return
            await send(message)
        try:
            guard=self.guards.setdefault(limit,RequestBodyLimitMiddleware(self.app,max_body_size=limit))
            await guard(scope,receive,guarded_send)
        except Exception as error:
            logging.getLogger('uvicorn.error').error('local_request_failed request_id=%s type=%s',identifier,type(error).__name__)
            if not started:await reject(500,'本地操作未完成，原记录保留；请凭请求编号查看记录。')
        finally:
            self.active-=1;self.uploads-=int(large)


def status():
    path=ROOT/'.env';protected=not path.exists() or (not path.is_symlink() and (os.name!='posix' or path.stat().st_mode&0o077==0))
    return {'scope':'personal_local_only','login_required':False,'peer_loopback_required':True,'host_and_origin_checks':True,
            'request_body_limits':True,'max_active_requests':48,'max_large_uploads':2,'max_queued_runs':MAX_QUEUED_RUNS,'child_output_limit_bytes':OUTPUT_LIMIT,
            'secret_file_owner_only':protected,'operational_log_redaction':True,'docs_exposed':False,'verified':False}
