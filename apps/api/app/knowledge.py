"""Knowledge retrieval and ingestion contracts. External RAG remains uninstalled."""
from __future__ import annotations

import json
from typing import Protocol, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import store

router=APIRouter(prefix='/api/v1/knowledge')


class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid')


class RetrievalRequest(Contract):
    query: str=Field(min_length=1,max_length=4000)
    collection_ids: list[str]=Field(default_factory=list,max_length=12)
    top_k: int=Field(default=5,ge=1,le=10)
    max_characters: int=Field(default=12000,ge=1000,le=30000)


class Source(Contract):
    source_id: str=Field(min_length=1,max_length=200)
    title: str=Field(min_length=1,max_length=300)
    uri: str | None=Field(default=None,max_length=2000)
    location: str | None=Field(default=None,max_length=300)


class Chunk(Contract):
    chunk_id: str=Field(min_length=1,max_length=200)
    content: str=Field(min_length=1,max_length=30000)
    source: Source
    score: float | None=Field(default=None,ge=0,le=1,allow_inf_nan=False)


class RetrievalResult(Contract):
    protocol_version: Literal[1]=1
    status: Literal['ok','disabled','not_connected','error']
    provider_id: str
    chunks: list[Chunk]=Field(default_factory=list,max_length=10)
    error_code: str | None=None
    message: str=''
    duration_ms: int=Field(default=0,ge=0)


class ProviderConfig(Contract):
    revision: int=Field(default=0,ge=0)
    provider_id: Literal['disabled','external_pending','local_full_text','dify_cloud']='disabled'
    enabled: bool=False
    endpoint: str=Field(default='',max_length=1000)
    collection_ids: list[str]=Field(default_factory=list,max_length=12)
    credential_env: str=Field(default='RAG_API_KEY',pattern=r'^RAG_[A-Z0-9_]{1,64}$')
    timeout_seconds: int=Field(default=15,ge=1,le=60)

    @model_validator(mode='after')
    def reserved_only(self):
        if self.enabled and self.provider_id not in {'local_full_text','dify_cloud'}: raise ValueError('尚未安装外部RAG适配器；目前只能启用本地全文检索。')
        if self.provider_id=='dify_cloud' and (self.endpoint not in {'','https://api.dify.ai/v1'} or self.credential_env!='RAG_API_KEY'):
            raise ValueError('Dify仅使用官方地址与RAG_API_KEY。')
        if self.endpoint:
            parsed=urlsplit(self.endpoint)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('地址须为HTTP(S)服务地址，不能包含密钥、账号、查询参数或片段。')
        return self


class KnowledgeProvider(Protocol):
    """Future vendor adapters normalize their output to this bounded contract.

    Future transport must honor timeout, redirect/host policy and credential isolation.
    Only explicit retrieval may contact the selected provider; generated code gets no key.
    """
    provider_id: str
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class DisabledProvider:
    provider_id='disabled'
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(status='disabled',provider_id=self.provider_id,message='RAG未启用，核心流程继续使用需求和用户选择的文本参考资料。')


class PendingProvider:
    provider_id='external_pending'
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(status='not_connected',provider_id=self.provider_id,error_code='adapter_not_installed',message='接口已预留，等待选择并安装现成RAG；没有发送网络请求。')


class LocalTextProvider:
    provider_id='local_full_text'
    def retrieve(self, request:RetrievalRequest)->RetrievalResult:
        from . import library
        settings=config()
        ids=request.collection_ids or settings.collection_ids
        if not ids:return RetrievalResult(status='error',provider_id=self.provider_id,error_code='collection_required',message='请明确选择知识库。')
        try:
            raw=library.search(library.SearchInput(query=request.query,collection_ids=ids,top_k=request.top_k,max_characters=request.max_characters))
            chunks=[Chunk(chunk_id=item['chunk_id'],content=item['content'],source=Source(**item['source']),score=None) for item in raw['chunks']]
            return RetrievalResult(status='ok',provider_id=self.provider_id,chunks=chunks,message=raw['message'],duration_ms=raw['duration_ms'])
        except (library.KnowledgeError,ValueError,HTTPException):
            return RetrievalResult(status='error',provider_id=self.provider_id,error_code='local_index_unavailable',message='所选知识库不存在、尚未就绪，或本地索引不可用。')


class DifyProvider:
    provider_id='dify_cloud'
    def retrieve(self,request:RetrievalRequest)->RetrievalResult:
        from . import dify
        settings=config()
        try:
            value=dify.capture(request.collection_ids or settings.collection_ids,request.query,min(request.top_k,3),request.max_characters)
            if not value:return RetrievalResult(status='error',provider_id=self.provider_id,error_code='collection_required',message='请明确选择Dify知识库。')
            return RetrievalResult(status='ok',provider_id=self.provider_id,message=value['message'],chunks=[Chunk(chunk_id=item['chunk_id'],content=item['content'],source=Source(**item['source']),score=item.get('score')) for item in value['chunks']])
        except (dify.DifyError,ValueError):return RetrievalResult(status='error',provider_id=self.provider_id,error_code='dify_unavailable',message='Dify未就绪或已达到本地节约限额；未回退付费模型。')


class IndexTaskResult(Contract):
    provider_id:str
    collection_id:str
    document_id:str
    job_id:str
    status:Literal['queued','parsing','chunking','indexing','ready','failed','needs_ocr','interrupted']
    progress:int=Field(ge=0,le=100)
    error_code:str|None=None
    message:str=''


class KnowledgeIngestionProvider(Protocol):
    """Optional capability for future remote providers. No hidden uploads on retrieval."""
    provider_id:str
    def create_collection(self,name:str,description:str)->dict: ...
    def upload_document(self,collection_id:str,filename:str,content:bytes)->IndexTaskResult: ...
    def index_status(self,collection_id:str,document_id:str,job_id:str)->IndexTaskResult: ...
    def delete_document(self,collection_id:str,document_id:str)->None: ...


PROVIDERS: dict[str,KnowledgeProvider]={'disabled':DisabledProvider(),'external_pending':PendingProvider(),'local_full_text':LocalTextProvider(),'dify_cloud':DifyProvider()}


def connection():
    conn=store.connect()
    conn.execute('CREATE TABLE IF NOT EXISTS knowledge_config (id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)')
    conn.commit()
    return conn


def config() -> ProviderConfig:
    conn=connection()
    try:
        row=conn.execute('SELECT value FROM knowledge_config WHERE id=1').fetchone()
        return ProviderConfig.model_validate(json.loads(row['value'])) if row else ProviderConfig()
    finally: conn.close()


def retrieve(request: RetrievalRequest) -> RetrievalResult:
    settings=config()
    selected=settings.provider_id if settings.enabled or settings.provider_id=='external_pending' else 'disabled'
    result=PROVIDERS[selected].retrieve(request)
    if sum(len(chunk.content) for chunk in result.chunks)>request.max_characters or len(result.chunks)>request.top_k:
        return RetrievalResult(status='error',provider_id=settings.provider_id,error_code='response_limit',message='检索响应超过约定上限，未注入模型上下文。')
    return result


@router.get('/contract')
def contract():
    return {'protocol_version':1,'implementation':'local_full_text_and_external_contract','network_enabled':True,
        'ingestion':{'local_base_url':'/api/v1/library','external_adapter_installed':True,'index_task_schema':IndexTaskResult.model_json_schema()},
        'providers':[{'id':'dify_cloud','label':'Dify Cloud（免费额度保护）'},{'id':'local_full_text','label':'本地全文检索'},{'id':'disabled','label':'关闭'},{'id':'external_pending','label':'外部RAG（等待适配）'}],
        'request_schema':RetrievalRequest.model_json_schema(),'response_schema':RetrievalResult.model_json_schema()}


@router.get('/config')
def read_config():return config().model_dump()


@router.put('/config')
def save_config(payload: ProviderConfig):
    conn=connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT value FROM knowledge_config WHERE id=1').fetchone()
        revision=json.loads(row['value'])['revision'] if row else 0
        if payload.revision!=revision: raise HTTPException(409,'RAG配置已变化，请重新读取。')
        value=payload.model_copy(update={'revision':revision+1})
        conn.execute('INSERT OR REPLACE INTO knowledge_config VALUES (1,?)',(value.model_dump_json(),));conn.commit()
        return value.model_dump()
    finally: conn.close()


@router.post('/retrieve',response_model=RetrievalResult)
def retrieve_endpoint(payload: RetrievalRequest):
    return retrieve(payload)
