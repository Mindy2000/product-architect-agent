"""Document lifecycle and an SQLite full-text backend, independent of model providers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path

from .document_runtime import capabilities as document_capabilities, extract_document, DocumentRuntimeError

from fastapi import APIRouter,BackgroundTasks,HTTPException,Request,Response
from pydantic import BaseModel,ConfigDict,Field

ROOT=Path(__file__).resolve().parents[3]
DATA=ROOT/'data'/'knowledge'
MAX_FILE_BYTES=20*1024*1024
MAX_LIBRARY_BYTES=256*1024*1024
MAX_COLLECTIONS=30
MAX_DOCUMENTS=500
MAX_CHUNKS=50000
_processing=threading.BoundedSemaphore(2)
ACTIVE={'uploading','queued','parsing','chunking','indexing'}
router=APIRouter(prefix='/api/v1/library')


class KnowledgeError(Exception):pass


class CollectionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=100)
    description:str=Field(default='',max_length=600)


class SearchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=4000)
    collection_ids:list[uuid.UUID]=Field(min_length=1,max_length=6)
    top_k:int=Field(default=5,ge=1,le=10)
    max_characters:int=Field(default=12000,ge=1000,le=30000)
    document_ids:list[uuid.UUID]=Field(default_factory=list,max_length=50)
    minimum_token_coverage:float=Field(default=0.2,ge=0,le=1)


def now():return datetime.now(timezone.utc).isoformat()


def connect():
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
    conn=sqlite3.connect(DATA/'library.sqlite3',timeout=20)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
      filename TEXT NOT NULL,bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL,
      error TEXT NOT NULL,warnings TEXT NOT NULL,page_count INTEGER NOT NULL,chunk_count INTEGER NOT NULL,
      job_id TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      collection_id TEXT NOT NULL,ordinal INTEGER NOT NULL,page INTEGER,line_start INTEGER,line_end INTEGER,
      content TEXT NOT NULL,start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL,
      error TEXT NOT NULL,started_at TEXT,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS document_versions(document_id TEXT PRIMARY KEY,supersedes TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS retrievals(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,request_json TEXT NOT NULL,result_json TEXT NOT NULL);
    ''')
    try:conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(chunk_id UNINDEXED,terms)')
    except sqlite3.OperationalError:pass
    if 'cancel_requested' not in {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}:conn.execute('ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0')
    conn.commit()
    return conn


def raw_path(identifier:str):
    directory=DATA/'uploads'
    if DATA.is_symlink() or directory.is_symlink():raise KnowledgeError('资料目录路径异常。')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(str(uuid.UUID(identifier))+'.blob')
    if path.is_symlink():raise KnowledgeError('资料文件路径异常。')
    return path


def collection_rows(ids:list[str],require_ready=False):
    if not ids:return []
    if len(ids)>6 or len(set(ids))!=len(ids):raise HTTPException(422,'最多选择6个不同的知识库。')
    conn=connect()
    try:
        result=[]
        for identifier in ids:
            row=conn.execute('SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.collection_id=c.id AND d.status=\'ready\') AS ready_documents FROM collections c WHERE id=?',(str(uuid.UUID(identifier)),)).fetchone()
            if not row:raise HTTPException(409,'部分知识库已被删除，请重新选择。')
            if require_ready and not row['ready_documents']:raise HTTPException(409,f"知识库“{row['name']}”尚无索引完成的资料。")
            result.append(dict(row))
        return result
    finally:conn.close()


def document(row):
    value=dict(row)
    value['warnings']=json.loads(value['warnings'])
    started=datetime.fromisoformat(value['created_at']).timestamp();end=time.time() if value['status'] in ACTIVE else datetime.fromisoformat(value['updated_at']).timestamp()
    value['elapsed_seconds']=round(max(0,end-started),1);value['can_cancel']=value['status'] in ACTIVE
    value['can_retry']=value['status'] in {'failed','needs_ocr','interrupted','ready','cancelled'}
    return value


def tokenize(text:str):
    words=re.findall(r'[a-zA-Z0-9_]{2,64}',text.lower())
    for group in re.findall(r'[\u3400-\u9fff]+',text):
        words.extend(group[index:index+2] for index in range(len(group)-1))
        if len(group)==1:words.append(group)
    return list(dict.fromkeys(words))


def chunk_pages(pages:list[dict]):
    result=[]
    for page in pages:
        text=page['text']
        start=0
        while start<len(text):
            end=min(start+1200,len(text))
            if end<len(text):
                boundary=text.rfind('\n',start+600,end)
                if boundary>start:end=boundary+1
            content=text[start:end]
            if content.strip():result.append({'page':page['page'],'content':content,'start_offset':start,'end_offset':end,
                'line_start':text.count('\n',0,start)+1,'line_end':text.count('\n',0,end)+1})
            if len(result)>2200:raise KnowledgeError('资料分段数量超过2200段，请拆分文件。')
            if end>=len(text):break
            start=max(start+1,end-150)
    return result


def set_phase(doc_id,job_id,status,progress,error='',warnings=None):
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT job_id FROM documents WHERE id=?',(doc_id,)).fetchone()
        if not row or row['job_id']!=job_id or conn.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job_id,)).fetchone()[0]:return False
        conn.execute('UPDATE documents SET status=?,progress=?,error=?,updated_at=? WHERE id=?',(status,progress,error,now(),doc_id))
        if warnings is not None:conn.execute('UPDATE documents SET warnings=? WHERE id=?',(json.dumps(warnings,ensure_ascii=False),doc_id))
        conn.execute('UPDATE jobs SET status=?,progress=?,error=?,started_at=COALESCE(started_at,?),completed_at=? WHERE id=?',
            (status,progress,error,now(),None if status in ACTIVE else now(),job_id))
        conn.commit();return True
    finally:conn.close()


def extract(doc:dict):
    raw=raw_path(doc['id']).read_bytes()
    if len(raw)>MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest()!=doc['sha256']:raise KnowledgeError('资料文件大小或校验值不匹配。')
    suffix=Path(doc['filename']).suffix.lower()
    if suffix in {'.pdf','.docx','.pptx','.xlsx'}:
        try:return extract_document(raw,suffix)
        except DocumentRuntimeError as error:raise KnowledgeError(str(error)) from error
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:raise KnowledgeError('文本须为UTF-8编码，请转换后重新上传。')
    if '\x00' in text:raise KnowledgeError('文件包含无效文本内容。')
    if len(text)>2000000:raise KnowledgeError('提取文本超过200万字符，请拆分后上传。')
    return [{'page':None,'text':text}]


def job_cancelled(job_id):
    conn=connect()
    try:
        row=conn.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job_id,)).fetchone()
        return not row or bool(row[0])
    finally:conn.close()


def process_document(doc_id:str,job_id:str):
    from .document_runtime import cancellation
    with _processing,cancellation(lambda:job_cancelled(job_id)):
        if not job_cancelled(job_id):_process_document(doc_id,job_id)


def _process_document(doc_id:str,job_id:str):
    conn=connect()
    try:
        row=conn.execute('SELECT * FROM documents WHERE id=? AND job_id=?',(doc_id,job_id)).fetchone()
        if not row:return
        doc=dict(row)
    finally:conn.close()
    try:
        if not set_phase(doc_id,job_id,'parsing',15):return
        pages=extract(doc)
        conn=connect()
        try:
            conn.execute('UPDATE documents SET page_count=? WHERE id=? AND job_id=? AND NOT EXISTS (SELECT 1 FROM jobs WHERE id=? AND cancel_requested=1)',(len(pages),doc_id,job_id,job_id));conn.commit()
        finally:conn.close()
        empty=[page['page'] for page in pages if page['page'] and not page['text'].strip()]
        warnings=[f"以下页/文本单元无可提取文字，未索引：{','.join(map(str,empty[:30]))}"] if empty else []
        ocr_pages=[page['page'] for page in pages if page.get('method')=='local_ocr']
        if ocr_pages:warnings.append('已使用本地免费OCR识别以下页：'+','.join(map(str,ocr_pages))+'。识别可能有误，请核对引用原页。')
        if doc['filename'].lower().endswith(('.docx','.pptx','.xlsx')):
            warnings.append('提取Office正文、幻灯片文字或单元格缓存值；不执行公式、宏或外链，不识别嵌入图片。页码指幻灯片/工作表序号；DOCX按文本行定位。')
        if not any(page['text'].strip() for page in pages):
            set_phase(doc_id,job_id,'needs_ocr' if doc['filename'].lower().endswith('.pdf') else 'failed',0,
                '本地OCR后未提取到可索引文字，请检查扫描清晰度或换用文字版PDF。' if empty else '资料没有可索引文字。',warnings);return
        if not set_phase(doc_id,job_id,'chunking',45,warnings=warnings):return
        chunks=chunk_pages(pages)
        if not set_phase(doc_id,job_id,'indexing',70):return
        conn=connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            current=conn.execute('SELECT job_id FROM documents WHERE id=?',(doc_id,)).fetchone()
            if not current or current['job_id']!=job_id or conn.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job_id,)).fetchone()[0]:return
            if conn.execute('SELECT COUNT(*) FROM chunks WHERE document_id!=?',(doc_id,)).fetchone()[0]+len(chunks)>MAX_CHUNKS:raise KnowledgeError('本地索引已达到50000段上限，请删除不再使用的资料。')
            conn.execute('DELETE FROM chunk_search WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)',(doc_id,))
            conn.execute('DELETE FROM chunks WHERE document_id=?',(doc_id,))
            for index,chunk in enumerate(chunks):
                identifier=str(uuid.uuid4())
                conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)',(identifier,doc_id,doc['collection_id'],index,chunk['page'],chunk['line_start'],chunk['line_end'],chunk['content'],chunk['start_offset'],chunk['end_offset']))
                conn.execute('INSERT INTO chunk_search VALUES (?,?)',(identifier,' '.join(tokenize(chunk['content']))))
            conn.execute('UPDATE documents SET status=\'ready\',progress=100,error=\'\',page_count=?,chunk_count=?,updated_at=? WHERE id=?',(len(pages),len(chunks),now(),doc_id))
            conn.execute('UPDATE jobs SET status=\'ready\',progress=100,completed_at=? WHERE id=?',(now(),job_id))
            conn.commit()
        finally:conn.close()
    except Exception as error:
        if job_cancelled(job_id):return
        message=str(error) if isinstance(error,KnowledgeError) else '索引未完成，原文件保留；请检查解析依赖或稍后重试。'
        set_phase(doc_id,job_id,'failed',0,message)


@router.get('/capabilities')
def local_capabilities():
    return {'formats':['pdf','txt','md','markdown','docx','pptx','xlsx'],'ocr':document_capabilities()}


@router.get('/collections')
def list_collections():
    conn=connect()
    try:
        rows=conn.execute('SELECT c.*,COUNT(d.id) AS document_count,COALESCE(SUM(d.status=\'ready\'),0) AS ready_documents FROM collections c LEFT JOIN documents d ON d.collection_id=c.id GROUP BY c.id ORDER BY c.created_at DESC').fetchall()
        return {'items':[dict(row) for row in rows],'backend':'local_full_text','semantic_search':False,
            'limits':{'file_bytes':MAX_FILE_BYTES,'library_bytes':MAX_LIBRARY_BYTES,'documents':MAX_DOCUMENTS,'pdf_pages':200}}
    finally:conn.close()


@router.post('/collections')
def create_collection(payload:CollectionInput):
    if not payload.name.strip():raise HTTPException(422,'请输入知识库名称。')
    value={'id':str(uuid.uuid4()),'name':payload.name.strip(),'description':payload.description,'created_at':now()}
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT COUNT(*) FROM collections').fetchone()[0]>=MAX_COLLECTIONS:raise HTTPException(409,'最多创建30个知识库。')
        conn.execute('INSERT INTO collections VALUES (:id,:name,:description,:created_at)',value);conn.commit()
        return {**value,'document_count':0,'ready_documents':0}
    finally:conn.close()


@router.get('/collections/{collection_id}/documents')
def list_documents(collection_id:uuid.UUID):
    collection_rows([str(collection_id)])
    conn=connect()
    try:return {'items':[document(row) for row in conn.execute("SELECT d.*,(SELECT v.document_id FROM document_versions v JOIN documents n ON n.id=v.document_id WHERE v.supersedes=d.id AND n.status='ready' ORDER BY n.created_at DESC LIMIT 1) AS superseded_by FROM documents d WHERE d.collection_id=? ORDER BY d.created_at DESC",(str(collection_id),)).fetchall()]}
    finally:conn.close()


@router.post('/collections/{collection_id}/documents')
async def upload_document(collection_id:uuid.UUID,request:Request,background_tasks:BackgroundTasks,filename:str,replaces:uuid.UUID|None=None):
    collection_rows([str(collection_id)])
    if not 1<=len(filename)<=180 or '/' in filename or '\\' in filename or not filename.lower().endswith(('.pdf','.txt','.md','.markdown','.docx','.pptx','.xlsx')):raise HTTPException(422,'请选择PDF、TXT、Markdown、DOCX、PPTX或XLSX文件，文件名不能包含目录。')
    if replaces:
        previous=get_document(replaces)
        if previous.get('superseded_by'):raise HTTPException(409,'此资料已有更新版本，请选择最新版本继续更新。')
        if previous['collection_id']!=str(collection_id) or previous['status'] in ACTIVE:raise HTTPException(409,'只能更新当前知识库中处理已结束的资料。')
    identifier,job_id=str(uuid.uuid4()),str(uuid.uuid4())
    path=raw_path(identifier)
    created=now()
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        quota=conn.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM documents').fetchone()
        if quota[0]>=MAX_DOCUMENTS or quota[1]+MAX_FILE_BYTES>MAX_LIBRARY_BYTES:raise HTTPException(413,'资料库容量不足，单份上传需预留20MiB。')
        conn.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,str(collection_id),filename,MAX_FILE_BYTES,'','uploading',0,'','[]',0,0,job_id,created,created))
        conn.execute('INSERT INTO jobs(id,document_id,status,progress,error,started_at,completed_at) VALUES (?,?,?,0,\'\',NULL,NULL)',(job_id,identifier,'uploading'));conn.commit()
    finally:conn.close()
    try:
        size=0;sha=hashlib.sha256()
        with path.open('xb') as stream:
            os.chmod(path,0o600)
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_FILE_BYTES:raise HTTPException(413,'单份文件不能超过20MiB。')
                stream.write(chunk);sha.update(chunk)
        if not size:raise HTTPException(422,'不能上传空文件。')
        if filename.lower().endswith('.pdf'):
            with path.open('rb') as stream:
                if b'%PDF-' not in stream.read(1024):raise HTTPException(422,'文件内容不是有效PDF。')
        conn=connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            duplicate=conn.execute("SELECT id FROM documents WHERE collection_id=? AND sha256=? AND id!=? AND status!='failed' ORDER BY created_at DESC LIMIT 1",(str(collection_id),sha.hexdigest(),identifier)).fetchone()
            if duplicate:
                conn.execute('DELETE FROM jobs WHERE document_id=?',(identifier,));conn.execute('DELETE FROM documents WHERE id=?',(identifier,));conn.commit()
                path.unlink(missing_ok=True)
                return {**get_document(uuid.UUID(duplicate['id'])),'duplicate':True}
            conn.execute('UPDATE documents SET bytes=?,sha256=? WHERE id=?',(size,sha.hexdigest(),identifier))
            if replaces:conn.execute('INSERT INTO document_versions VALUES (?,?)',(identifier,str(replaces)))
            conn.commit()
        finally:conn.close()
        set_phase(identifier,job_id,'queued',5)
        background_tasks.add_task(process_document,identifier,job_id)
        return {'id':identifier,'collection_id':str(collection_id),'filename':filename,'status':'queued','progress':5,'job_id':job_id}
    except BaseException:
        path.unlink(missing_ok=True)
        set_phase(identifier,job_id,'failed',0,'上传未完成，请重新上传。')
        conn=connect()
        try:conn.execute('UPDATE documents SET bytes=0 WHERE id=?',(identifier,));conn.commit()
        finally:conn.close()
        raise


@router.get('/documents/{document_id}')
def get_document(document_id:uuid.UUID):
    conn=connect()
    try:
        row=conn.execute("SELECT d.*,(SELECT v.document_id FROM document_versions v JOIN documents n ON n.id=v.document_id WHERE v.supersedes=d.id AND n.status='ready' ORDER BY n.created_at DESC LIMIT 1) AS superseded_by FROM documents d WHERE d.id=?",(str(document_id),)).fetchone()
        if not row:raise HTTPException(404,'未找到资料。')
        return document(row)
    finally:conn.close()


@router.get('/jobs/{job_id}')
def get_job(job_id:uuid.UUID):
    conn=connect()
    try:
        row=conn.execute('SELECT j.*,d.collection_id,d.filename FROM jobs j JOIN documents d ON d.id=j.document_id WHERE j.id=?',(str(job_id),)).fetchone()
        if not row:raise HTTPException(404,'未找到索引任务。')
        value=dict(row);started=datetime.fromisoformat(value['started_at'] or get_document(uuid.UUID(value['document_id']))['created_at']).timestamp();ended=datetime.fromisoformat(value['completed_at']).timestamp() if value['completed_at'] else time.time()
        return {**value,'provider_id':'local_full_text','elapsed_seconds':round(max(0,ended-started),1),'can_cancel':value['status'] in ACTIVE}
    finally:conn.close()


@router.get('/documents/{document_id}/chunks')
def preview_chunks(document_id:uuid.UUID,offset:int=0,limit:int=10):
    if offset<0 or not 1<=limit<=20:raise HTTPException(422,'分页参数无效。')
    doc=get_document(document_id)
    conn=connect()
    try:return {'document':doc,'items':[dict(row) for row in conn.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal LIMIT ? OFFSET ?',(str(document_id),limit,offset)).fetchall()]}
    finally:conn.close()


@router.post('/documents/{document_id}/retry')
def retry_document(document_id:uuid.UUID,background_tasks:BackgroundTasks):
    identifier=str(document_id);job_id=str(uuid.uuid4())
    if not raw_path(identifier).is_file():raise HTTPException(409,'原始文件未保留，请重新上传。')
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT status FROM documents WHERE id=?',(identifier,)).fetchone()
        if not row:raise HTTPException(404,'未找到资料。')
        if row['status'] not in {'failed','interrupted','needs_ocr','ready','cancelled'}:raise HTTPException(409,'仅可对处理已结束的资料重建索引。')
        conn.execute('UPDATE documents SET status=\'queued\',progress=5,error=\'\',job_id=?,updated_at=? WHERE id=?',(job_id,now(),identifier))
        conn.execute('INSERT INTO jobs(id,document_id,status,progress,error,started_at,completed_at) VALUES (?,?,?,5,\'\',NULL,NULL)',(job_id,identifier,'queued'));conn.commit()
    finally:conn.close()
    background_tasks.add_task(process_document,identifier,job_id)
    return {'id':identifier,'status':'queued','job_id':job_id}


@router.delete('/documents/{document_id}',status_code=204)
def delete_document(document_id:uuid.UUID):
    identifier=str(document_id)
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT status FROM documents WHERE id=?',(identifier,)).fetchone()
        if not row:raise HTTPException(404,'未找到资料。')
        if row['status'] in ACTIVE:raise HTTPException(409,'请等待当前处理结束后删除资料。')
        conn.execute('DELETE FROM chunk_search WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)',(identifier,))
        conn.execute('DELETE FROM retrievals WHERE instr(result_json,?)>0',(identifier,))
        conn.execute('DELETE FROM jobs WHERE document_id=?',(identifier,))
        conn.execute('DELETE FROM documents WHERE id=?',(identifier,));conn.commit()
    finally:conn.close()
    raw_path(identifier).unlink(missing_ok=True)
    return Response(status_code=204)


@router.delete('/collections/{collection_id}',status_code=204)
def delete_collection(collection_id:uuid.UUID):
    identifier=str(collection_id)
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        docs=conn.execute('SELECT id,status FROM documents WHERE collection_id=?',(identifier,)).fetchall()
        if any(row['status'] in ACTIVE for row in docs):raise HTTPException(409,'知识库有处理中资料，请稍后删除。')
        conn.execute('DELETE FROM chunk_search WHERE chunk_id IN (SELECT id FROM chunks WHERE collection_id=?)',(identifier,))
        conn.execute('DELETE FROM retrievals WHERE instr(result_json,?)>0',(identifier,))
        conn.execute('DELETE FROM jobs WHERE document_id IN (SELECT id FROM documents WHERE collection_id=?)',(identifier,))
        conn.execute('DELETE FROM collections WHERE id=?',(identifier,));conn.commit()
    finally:conn.close()
    for row in docs:raw_path(row['id']).unlink(missing_ok=True)
    return Response(status_code=204)


def search(payload:SearchInput,record=True):
    started=time.monotonic()
    ids=[str(item) for item in payload.collection_ids]
    collections=collection_rows(ids,require_ready=True)
    words=tokenize(payload.query)[:64]
    result={'protocol_version':1,'provider_id':'local_full_text','status':'ok','chunks':[],
        'message':'本地关键词全文检索，未使用Embedding或语义检索。','retrieval_id':str(uuid.uuid4()),'collections':collections,'query':payload.query}
    conn=connect()
    try:
        if words:
            match=' OR '.join('"'+word.replace('"','""')+'"' for word in words)
            placeholders=','.join('?' for _ in ids)
            doc_ids=[str(item) for item in payload.document_ids]
            document_clause=' AND c.document_id IN ('+','.join('?' for _ in doc_ids)+')' if doc_ids else ''
            rows=conn.execute(f'''SELECT c.*,d.filename,d.sha256 AS document_sha256,bm25(chunk_search) AS rank
                FROM chunk_search JOIN chunks c ON c.id=chunk_search.chunk_id JOIN documents d ON d.id=c.document_id
                WHERE chunk_search MATCH ? AND c.collection_id IN ({placeholders}) AND d.status='ready' {document_clause}
                AND NOT EXISTS(SELECT 1 FROM document_versions v JOIN documents newer ON newer.id=v.document_id WHERE v.supersedes=d.id AND newer.status='ready')
                ORDER BY rank,c.id LIMIT 100''',(match,*ids,*doc_ids)).fetchall()
            remaining=payload.max_characters
            selected_docs={str(item) for item in payload.document_ids}
            ranked=[]
            for row in rows:
                if selected_docs and row['document_id'] not in selected_docs:continue
                matched=[word for word in words if word in tokenize(row['content'])]
                coverage=len(matched)/len(words) if words else 0
                if coverage<payload.minimum_token_coverage:continue
                ranked.append((coverage,row,matched))
            ranked.sort(key=lambda item:(-item[0],item[1]['rank'],item[1]['id']))
            for coverage,row,matched in ranked[:payload.top_k]:
                content=row['content'][:remaining]
                if not content:break
                remaining-=len(content)
                location=f"第{row['page']}页" if row['page'] else f"第{row['line_start']}–{row['line_end']}行"
                result['chunks'].append({'chunk_id':row['id'],'content':content,'score':None,
                    'collection_id':row['collection_id'],'document_id':row['document_id'],'document_sha256':row['document_sha256'],'token_coverage':round(coverage,4),'matched_keywords':matched,
                    'source':{'source_id':row['document_id'],'title':row['filename'],'uri':f"knowledge://{row['collection_id']}/{row['document_id']}",'location':location}})
        result['duration_ms']=int((time.monotonic()-started)*1000)
        if record:
            conn.execute('INSERT INTO retrievals VALUES (?,?,?,?)',(result['retrieval_id'],now(),payload.model_dump_json(),json.dumps(result,ensure_ascii=False)))
            conn.execute('DELETE FROM retrievals WHERE id IN (SELECT id FROM retrievals ORDER BY created_at DESC LIMIT -1 OFFSET 200)');conn.commit()
        return result
    except sqlite3.OperationalError as error:raise KnowledgeError('本地全文索引不可用，请确认Python SQLite支持FTS5。') from error
    finally:conn.close()


@router.post('/retrieve')
def search_endpoint(payload:SearchInput):
    try:return search(payload)
    except KnowledgeError as error:raise HTTPException(409,str(error))


def capture(ids:list[str],query:str):
    if not ids:return None
    try:return search(SearchInput(collection_ids=ids,query=query[:4000]))
    except KnowledgeError as error:raise HTTPException(409,str(error))


def sources(context:dict|None):
    return [{key:value for key,value in chunk.items() if key!='content'} for chunk in (context or {}).get('chunks',[])]


def recover_interrupted():
    conn=connect()
    try:
        marks=','.join('?' for _ in ACTIVE)
        conn.execute(f'UPDATE documents SET status=\'interrupted\',error=\'服务已中断，请重试资料处理。\' WHERE status IN ({marks})',tuple(ACTIVE))
        conn.execute(f'UPDATE jobs SET status=\'interrupted\',error=\'服务已中断。\',completed_at=? WHERE status IN ({marks})',(now(),*ACTIVE));conn.commit()
    finally:conn.close()


class CitationCheck(BaseModel):
    chunks:list[dict]=Field(max_length=20)


@router.post('/citations/check')
def check_citations(payload:CitationCheck):
    conn=connect()
    try:
        result=[]
        for item in payload.chunks:
            if item.get('provider_id')=='dify_cloud':
                result.append({'chunk_id':item.get('chunk_id'),'status':'remote_snapshot','note':'远程冻结引用；未自动查询，节省额度'})
                continue
            identifier=item.get('document_id') or (item.get('source') or {}).get('source_id')
            row=conn.execute('SELECT sha256,status FROM documents WHERE id=?',(identifier,)).fetchone()
            newer=conn.execute("SELECT v.document_id FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.supersedes=? AND d.status='ready' LIMIT 1",(identifier,)).fetchone()
            state='deleted' if not row else 'superseded' if newer else 'changed' if item.get('document_sha256')!=row['sha256'] else 'ready' if row['status']=='ready' else 'not_ready'
            result.append({'chunk_id':item.get('chunk_id'),'document_id':identifier,'status':state,'replacement_id':newer['document_id'] if newer else None})
        return {'items':result,'note':'历史冻结片段仍保留；重新生成方案才更新引用。'}
    finally:conn.close()


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id:uuid.UUID):
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM jobs WHERE id=?',(str(job_id),)).fetchone()
        if not row:raise HTTPException(404,'索引任务不存在。')
        if row['status'] not in ACTIVE:raise HTTPException(409,'任务已结束，不能撤销已完成的索引。')
        conn.execute("UPDATE jobs SET cancel_requested=1,status='cancelled',completed_at=?,error='已取消，原始资料保留。' WHERE id=?",(now(),str(job_id)))
        existing=conn.execute('SELECT COUNT(*) FROM chunks WHERE document_id=?',(row['document_id'],)).fetchone()[0]
        conn.execute("UPDATE documents SET status=?,progress=CASE WHEN ? THEN 100 ELSE progress END,error=?,updated_at=? WHERE id=? AND job_id=?",('ready' if existing else 'cancelled',bool(existing),'重建已取消，原索引仍可用。' if existing else '已取消，原始资料保留；可重试。',now(),row['document_id'],str(job_id)));conn.commit()
    finally:conn.close()
    return {'cancel_requested':True,'raw_file_preserved':True,'message':'取消已收到，正在解析的进程会停止；原始资料保留。'}
