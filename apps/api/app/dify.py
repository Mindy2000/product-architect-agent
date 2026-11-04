"""Dify Cloud classic knowledge adapter: bounded transport, receipts and free-only policy."""
from __future__ import annotations
import hashlib,json,math,os,sqlite3,threading,time,uuid
from typing import Literal
from pathlib import Path
from urllib import request,error
import truststore
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[3]
DATA=ROOT/'data'/'dify'
BASE='https://api.dify.ai/v1'
MODEL='text-embedding-3-small'
PROVIDER='langgenius/openai/openai'
router=APIRouter(prefix='/api/v1/dify')
lock=threading.RLock()


class DifyError(Exception):pass


def connect():
    if DATA.is_symlink() or any((DATA/('state.sqlite3'+suffix)).is_symlink() for suffix in ('','-wal','-shm','-journal')):raise DifyError('Dify本地记录路径异常。')
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
    conn=sqlite3.connect(DATA/'state.sqlite3',timeout=20);conn.row_factory=sqlite3.Row
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,at REAL,method TEXT,path TEXT,status TEXT,kind TEXT);
    CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,at REAL,value_json TEXT);
    CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,name TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS documents(dataset_id TEXT,sha256 TEXT,local_id TEXT,name TEXT,remote_id TEXT,batch TEXT,status TEXT,PRIMARY KEY(dataset_id,sha256));
    CREATE TABLE IF NOT EXISTS controls(id INTEGER PRIMARY KEY CHECK(id=1),paused TEXT);
    ''')
    if 'credit_reserve' not in {row[1] for row in conn.execute('PRAGMA table_info(calls)')}:conn.execute('ALTER TABLE calls ADD COLUMN credit_reserve INTEGER NOT NULL DEFAULT 0')
    conn.commit();return conn


def credentials():
    values=dotenv_values(ROOT/'.env')
    key=(values.get('RAG_API_KEY') or '').strip()
    if not key:raise DifyError('Dify密钥尚未填写。')
    if (values.get('RAG_API_BASE_URL') or BASE).rstrip('/')!=BASE:raise DifyError('只允许连接官方Dify Cloud地址。')
    return key


def policy():
    try:value=json.loads((DATA/'free-policy.json').read_text())
    except (OSError,ValueError):raise DifyError('尚未确认Dify免费模型额度，已禁止向量调用。')
    if not isinstance(value,dict):raise DifyError('Dify免费策略格式异常，请重新确认。')
    if value.get('credential_fingerprint') and value['credential_fingerprint']!=hashlib.sha256(credentials().encode()).hexdigest():raise DifyError('Dify密钥已变化，请重新确认当前账户免费策略。')
    if value.get('model')!=MODEL or value.get('provider')!=PROVIDER or not value.get('free_only'):
        raise DifyError('Dify免费模型配置尚未确认。')
    return value


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def call(method,path,payload=None,*,kind='metadata',cache_seconds=0):
    key=credentials();identity=hashlib.sha256((key+'|'+method+'|'+path+'|'+json.dumps(payload,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
    if not path.startswith(('/datasets','/workspaces/current/models/')) or '..' in path:raise DifyError('不允许的Dify接口。')
    with lock:
        conn=connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            cached=conn.execute('SELECT * FROM cache WHERE id=?',(identity,)).fetchone()
            if cached and cache_seconds and time.time()-cached['at']<cache_seconds:return json.loads(cached['value_json'])
            paused=conn.execute('SELECT paused FROM controls WHERE id=1').fetchone()
            if paused and paused[0]:raise DifyError('Dify调用已停止：'+paused[0])
            credit_reserve=0
            if kind in {'index','retrieve'}:
                rules=policy()
                credit_reserve=5 if kind=='retrieve' else (math.ceil(len(str((payload or {}).get('text','')).encode('utf-8'))/400)+1)*5
                allocated=conn.execute('SELECT COALESCE(SUM(credit_reserve),0) FROM calls WHERE at>=?',(rules.get('guard_start',0),)).fetchone()[0]
                if rules.get('observed_used_credits',0)+allocated+credit_reserve>rules.get('local_total_credit_ceiling',60):raise DifyError('已达到本地保守体验额度保护线，已停止新的向量操作；不会充值、升级或切换付费模型。')
            now=time.time()
            if conn.execute('SELECT COUNT(*) FROM calls WHERE at>?',(now-60,)).fetchone()[0]>=4:raise DifyError('为节省免费额度，每分钟最多4次远程请求；请稍后再试。')
            if conn.execute('SELECT COUNT(*) FROM calls WHERE at>?',(now-86400,)).fetchone()[0]>=40:raise DifyError('已达到本地24小时40次请求上限，已停止新调用。')
            if kind in {'index','retrieve'} and conn.execute("SELECT COUNT(*) FROM calls WHERE at>? AND kind IN ('index','retrieve')",(now-86400,)).fetchone()[0]>=12:raise DifyError('已达到24小时12次向量操作保护上限，已停止新调用。')
            identifier=str(uuid.uuid4())
            conn.execute('INSERT INTO calls(id,at,method,path,status,kind,credit_reserve) VALUES (?,?,?,?,?,?,?)',(identifier,now,method,path,'reserved',kind,credit_reserve));conn.commit()
        finally:conn.close()
        body=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
        req=request.Request(BASE+path,data=body,method=method,headers={'Authorization':'Bearer '+key,'Accept':'application/json','Content-Type':'application/json'})
        # System CA validation remains enabled. No redirects, retries or alternate provider fallback.
        truststore.inject_into_ssl()
        try:
            with request.build_opener(NoRedirect).open(req,timeout=25) as response:
                content=response.read(2*1024*1024+1)
                if len(content)>2*1024*1024:raise DifyError('Dify响应超限。')
                value=json.loads(content)
                if not isinstance(value,dict):raise DifyError('Dify响应格式无效。')
        except error.HTTPError as exc:
            status=str(exc.code)
            # Do not expose response bodies, request headers or credentials.
            conn=connect()
            try:
                conn.execute('UPDATE calls SET status=? WHERE id=?',(status,identifier))
                if exc.code in {401,402,403,429}:conn.execute('INSERT OR REPLACE INTO controls VALUES (1,?)',('鉴权、额度或限流拒绝（HTTP '+status+'），请检查控制台；未自动重试。',))
                conn.commit()
            finally:conn.close()
            raise DifyError('Dify拒绝请求（HTTP '+status+'），未自动重试或升级。') from None
        except Exception as exc:
            conn=connect()
            try:
                conn.execute("UPDATE calls SET status='uncertain' WHERE id=?",(identifier,));conn.commit()
            finally:conn.close()
            raise DifyError('Dify连接或响应未完成；未自动重试，变更请求需核对控制台后处理。') from None
        conn=connect()
        try:
            conn.execute("UPDATE calls SET status='completed' WHERE id=?",(identifier,))
            if cache_seconds:conn.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(identity,time.time(),json.dumps(value,ensure_ascii=False)))
            conn.commit()
        finally:conn.close()
        return value


def owned(dataset_id):
    identifier=str(uuid.UUID(str(dataset_id)));conn=connect()
    try:
        if not conn.execute('SELECT 1 FROM collections WHERE id=?',(identifier,)).fetchone():raise DifyError('只能操作本项目已登记的Dify知识库。')
    finally:conn.close()
    return identifier


class Create(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=40)


@router.get('/status')
def status():
    conn=connect()
    try:
        pause=conn.execute('SELECT paused FROM controls WHERE id=1').fetchone()
        try:confirmed=policy();confirmed_at=confirmed.get('confirmed_at')
        except DifyError:confirmed={};confirmed_at=None
        allocated=conn.execute('SELECT COALESCE(SUM(credit_reserve),0) FROM calls WHERE at>=?',(confirmed.get('guard_start',0),)).fetchone()[0]
        return {'policy_revision':policy_revision(),'reserved_credits':allocated,'local_remaining_credits':max(0,confirmed.get('local_total_credit_ceiling',60)-confirmed.get('observed_used_credits',0)-allocated) if confirmed_at else 0,'configured':bool((dotenv_values(ROOT/'.env').get('RAG_API_KEY') or '').strip()),'free_only':True,'model':MODEL,
            'free_confirmed_at':confirmed_at,'last_observed_used_credits':confirmed.get('observed_used_credits') if confirmed_at else None,'local_credit_ceiling':confirmed.get('local_total_credit_ceiling',60),'paused':pause[0] if pause else None,'calls_last_24h':conn.execute('SELECT COUNT(*) FROM calls WHERE at>?',(time.time()-86400,)).fetchone()[0],
            'limits':{'requests_per_minute':4,'requests_per_24h':40,'vector_operations_per_24h':12},
            'collections':[dict(row) for row in conn.execute('SELECT * FROM collections ORDER BY created')],
            'documents':[dict(row) for row in conn.execute('SELECT * FROM documents ORDER BY dataset_id,name')]}
    finally:conn.close()


@router.post('/collections')
def create(payload:Create):
    policy()
    with lock:
        conn=connect()
        try:
            existing=conn.execute('SELECT id,name FROM collections WHERE name=?',(payload.name,)).fetchone()
            if existing:return dict(existing)
            pending=conn.execute("SELECT 1 FROM calls WHERE method='POST' AND path='/datasets' AND status IN ('reserved','uncertain') LIMIT 1").fetchone()
            if pending:raise DifyError('存在结果不确定的建库请求，请先核对Dify控制台，避免重复创建。')
        finally:conn.close()
        result=call('POST','/datasets',{'name':payload.name,'description':'Product Architect Agent免费额度小样本实验','permission':'only_me','indexing_technique':'high_quality',
            'embedding_model':MODEL,'embedding_model_provider':PROVIDER,'retrieval_model':{'search_method':'semantic_search','reranking_enable':False,'top_k':3,'score_threshold_enabled':False}})
        conn=connect()
        try:conn.execute('INSERT INTO collections VALUES (?,?,?)',(result['id'],payload.name,time.time()));conn.commit()
        finally:conn.close()
        return {'id':result['id'],'name':payload.name}


class Sync(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id:uuid.UUID


@router.post('/collections/{dataset_id}/sync')
def sync(dataset_id:uuid.UUID,payload:Sync):
    from . import library
    identifier=owned(dataset_id);doc=library.get_document(payload.document_id)
    if doc['status']!='ready':raise DifyError('先完成本地资料索引，再显式同步到Dify。')
    pages=library.extract(doc)
    text='\n\n'.join((f"[原文第{page['page']}页]\n" if page['page'] else '')+page['text'] for page in pages)
    return upload_text(identifier,doc['id'],doc['filename'],doc['sha256'],text)


def upload_text(dataset_id,local_id,name,sha,text):
    identifier=owned(dataset_id);policy()
    if len(text)>12000:raise DifyError('免费额度模式单份资料最多12000字符，请先拆分。')
    with lock:
        conn=connect()
        try:
            row=conn.execute('SELECT * FROM documents WHERE dataset_id=? AND sha256=?',(identifier,sha)).fetchone()
            if row:
                if row['status']=='uncertain':raise DifyError('上次上传结果不确定，请核对远程控制台，禁止盲目重复索引。')
                return {**dict(row),'reused':True}
            if conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]>=4:raise DifyError('免费小样本模式最多同步4份不同资料，已停止新增上传。')
            conn.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?)',(identifier,sha,local_id,name,'','','uncertain'));conn.commit()
        finally:conn.close()
        result=call('POST',f'/datasets/{identifier}/document/create-by-text',{'name':name,'text':text,'indexing_technique':'high_quality','doc_form':'text_model',
            'embedding_model':MODEL,'embedding_model_provider':PROVIDER,'process_rule':{'mode':'custom','rules':{'pre_processing_rules':[{'id':'remove_extra_spaces','enabled':True},{'id':'remove_urls_emails','enabled':False}],'segmentation':{'separator':'\n','max_tokens':500}}}},kind='index')
        conn=connect()
        try:
            conn.execute('UPDATE documents SET remote_id=?,batch=?,status=? WHERE dataset_id=? AND sha256=?',(result['document']['id'],result['batch'],result['document'].get('indexing_status','waiting'),identifier,sha));conn.execute('DELETE FROM cache');conn.commit()
        finally:conn.close()
        return {'remote_id':result['document']['id'],'batch':result['batch'],'status':result['document'].get('indexing_status','waiting')}


@router.post('/collections/{dataset_id}/refresh')
def refresh(dataset_id:uuid.UUID):
    identifier=owned(dataset_id)
    result=call('GET',f'/datasets/{identifier}/documents?page=1&limit=100',cache_seconds=60)
    conn=connect()
    try:
        for item in result.get('data',[]):conn.execute('UPDATE documents SET status=? WHERE dataset_id=? AND remote_id=?',(item.get('indexing_status','unknown'),identifier,item['id']))
        conn.commit()
    finally:conn.close()
    return status()


def capture(ids,query,top_k=3,max_characters=6000):
    if not ids:return None
    if len(set(ids))!=len(ids) or len(ids)>2:raise DifyError('最多选择2个不同的Dify知识库。')
    chunks=[];seen=set()
    for dataset in ids:
        identifier=owned(dataset)
        conn=connect()
        try:
            rows=conn.execute('SELECT * FROM documents WHERE dataset_id=?',(identifier,)).fetchall()
            if not any(row['status']=='completed' for row in rows):raise DifyError('Dify知识库尚无索引完成的资料，请刷新状态。')
        finally:conn.close()
        result=call('POST',f'/datasets/{identifier}/retrieve',{'query':query[:2000],'retrieval_model':{'search_method':'semantic_search','reranking_enable':False,'top_k':min(top_k,3),'score_threshold_enabled':False}},kind='retrieve',cache_seconds=86400)
        records=result.get('records',[])
        if not isinstance(records,list):raise DifyError('Dify检索结果格式异常，未采信来源。')
        for item in records:
            if not isinstance(item,dict):raise DifyError('Dify切片格式异常，未采信来源。')
            segment=item.get('segment') or {}
            if not isinstance(segment,dict):raise DifyError('Dify切片格式异常，未采信来源。')
            document=segment.get('document') or {};content=segment.get('content','')
            if not isinstance(document,dict) or not isinstance(content,str):raise DifyError('Dify来源格式异常，未采信来源。')
            if not content:continue
            score=item.get('score')
            score=min(1.0,max(0.0,float(score))) if isinstance(score,(int,float)) and math.isfinite(score) else None
            origin=next((row for row in rows if row['remote_id']==document.get('id')),None)
            if origin is None:continue
            chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'dify:'+identifier+':'+str(segment.get('id') or content)))
            if chunk_id in seen:continue
            seen.add(chunk_id)
            chunks.append({'chunk_id':chunk_id,'provider_id':'dify_cloud','collection_id':identifier,'document_id':document.get('id','unknown'),
                'document_sha256':origin['sha256'] if origin else None,'content':content,'score':score,
                'source':{'source_id':document.get('id') or chunk_id,'title':str(document.get('name') or (origin['name'] if origin else 'Dify资料'))[:300],
                    'uri':'dify://'+identifier+'/'+str(document.get('id','unknown')),'location':'远程切片 '+str(segment.get('position') or segment.get('id') or '')}})
    chunks.sort(key=lambda chunk:-(chunk['score'] or 0));remaining=max_characters;bounded=[]
    for chunk in chunks[:top_k]:
        if remaining<=0:break
        value={**chunk,'content':chunk['content'][:remaining]};remaining-=len(value['content']);bounded.append(value)
    return {'provider_id':'dify_cloud','status':'ok' if bounded else 'empty','query':query,'retrieval_id':str(uuid.uuid4()),'chunks':bounded,'message':'Dify远程语义检索；同查询复用24小时缓存，不进行问答生成。' if bounded else '没有可核验的命中片段；不编造来源，同查询复用缓存。'}


class Search(BaseModel):
    query:str=Field(min_length=1,max_length=2000)
    collection_ids:list[uuid.UUID]=Field(min_length=1,max_length=2)


@router.post('/retrieve')
def retrieve(payload:Search):return capture([str(item) for item in payload.collection_ids],payload.query)


class FreePolicyConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str = Field(pattern=r'^(missing|[0-9a-f]{64})$')
    observed_used_credits: int = Field(ge=0, le=1000000000)
    local_total_credit_ceiling: int = Field(ge=1, le=60)
    free_model_confirmed: Literal[True]
    no_paid_fallback: Literal[True]


class ResumeConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    console_checked: Literal[True]


def policy_revision():
    path = DATA/'free-policy.json'
    if path.is_symlink():raise DifyError('Dify免费策略路径异常。')
    if not path.exists():return 'missing'
    if path.stat().st_size > 16384:raise DifyError('Dify免费策略过大，请检查本地配置。')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@router.put('/policy')
def confirm_free_policy(payload: FreePolicyConfirmation):
    # This records the user's console observation only. It never contacts a provider.
    key = credentials()
    with lock:
        conn = connect()
        temporary = DATA/('.policy-'+uuid.uuid4().hex)
        try:
            if payload.expected_revision != policy_revision():raise HTTPException(409,'免费策略已变化，请刷新后再确认。')
            previous = json.loads((DATA/'free-policy.json').read_text()) if payload.expected_revision != 'missing' else {}
            if not isinstance(previous,dict):raise DifyError('旧策略无效，未覆盖；请先检查本地配置。')
            allocated = conn.execute('SELECT COALESCE(SUM(credit_reserve),0) FROM calls WHERE at>=?',(previous.get('guard_start',0),)).fetchone()[0]
            value = {'model':MODEL,'provider':PROVIDER,'free_only':True,
                     'observed_used_credits':max(payload.observed_used_credits,previous.get('observed_used_credits',0)),
                     'local_total_credit_ceiling':payload.local_total_credit_ceiling,
                     'guard_start':previous.get('guard_start',0),
                     'confirmed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                     'credential_fingerprint':hashlib.sha256(key.encode()).hexdigest()}
            with temporary.open('x',encoding='utf-8') as stream:
                temporary.chmod(0o600);json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,DATA/'free-policy.json')
        finally:
            conn.close();temporary.unlink(missing_ok=True)
    return {'network_requests':0,'ledger_preserved':True,'reserved_credits':allocated,
            'message':'已保存本机免费策略；原请求、预留额度和暂停状态保留，没有验证Key或发起远程请求。', 'status':status()}


@router.post('/resume')
def resume_after_console_check(payload: ResumeConfirmation):
    with lock:
        if payload.expected_revision != policy_revision():raise HTTPException(409,'免费策略已变化，请重新读取。')
        credentials();rules = policy();conn = connect()
        try:
            allocated = conn.execute('SELECT COALESCE(SUM(credit_reserve),0) FROM calls WHERE at>=?',(rules.get('guard_start',0),)).fetchone()[0]
            if rules.get('observed_used_credits',0)+allocated >= rules.get('local_total_credit_ceiling',60):raise DifyError('本地保护额度已不足，不能解除暂停；不会重置额度或自动升级。')
            conn.execute('DELETE FROM controls WHERE id=1');conn.commit()
        finally:conn.close()
    return {'network_requests':0,'ledger_preserved':True,'automatic_retry':False,
            'message':'已解除本机暂停，没有重试请求；下次远程操作仍须手动发起并受原有限额保护。不确定的建库/同步记录仍保留，需先核对控制台。', 'status':status()}
