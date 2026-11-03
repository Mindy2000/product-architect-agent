"""Optional public research, free adapters and immutable source snapshots."""
from __future__ import annotations
import base64
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Literal,Protocol
from urllib.parse import urlencode,urlsplit,urljoin,quote
from urllib.robotparser import RobotFileParser
from uuid import UUID,uuid4
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from .public_http import normalize

ROOT=Path(__file__).resolve().parents[3]
DATA=ROOT/'data'/'research'
MAX_SELECTED=8
CONTEXT_LIMIT=24000
TEXT_LIMIT=16000
router=APIRouter(prefix='/api/v1/research')
_gate=threading.BoundedSemaphore(2)

DOCS=[
 ('React官方文档','https://react.dev/learn','react 组件 状态 前端 component state'),
 ('TypeScript手册','https://www.typescriptlang.org/docs/handbook/intro.html','typescript types 类型 前端'),
 ('Vite官方指南','https://vite.dev/guide/','vite 构建 工程 前端'),
 ('Tailwind官方文档','https://tailwindcss.com/docs/installation/using-vite','tailwind css 样式 布局'),
 ('FastAPI官方教程','https://fastapi.tiangolo.com/tutorial/','fastapi python api 后端'),
 ('SQLite官方文档','https://www.sqlite.org/docs.html','sqlite sql 数据库 database'),
 ('Python SQLite文档','https://docs.python.org/3/library/sqlite3.html','python sqlite 数据库 事务 migration'),
 ('Playwright官方文档','https://playwright.dev/docs/intro','playwright 测试 浏览器 test'),
 ('MDN Web API','https://developer.mozilla.org/en-US/docs/Web/API','javascript html css web 浏览器 storage localstorage'),
 ('WCAG辅助功能规范','https://www.w3.org/WAI/standards-guidelines/wcag/','wcag accessibility 无障碍 可访问性'),
 ('GOV.UK用户需求指南','https://www.gov.uk/service-manual/user-research/start-by-learning-user-needs','产品 用户需求 product user needs'),
 ('GOV.UK服务设计','https://www.gov.uk/service-manual/design','设计 服务 产品 design product')]


class ResearchError(Exception):pass


class PublicReader(Protocol):
    def search(self,query:str)->dict:...
    def read(self,url:str)->dict:...


MCP_EXTENSION={'version':1,'status':'interface_only','registered_servers':[],
 'allowed_actions':['search','read'],'write_actions':False,'private_accounts':False,'automatic_discovery':False}


def connect():
    if (ROOT/'data').is_symlink() or DATA.is_symlink() or (DATA/'research.sqlite3').is_symlink():raise ResearchError('网页研究目录异常。')
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
    conn=sqlite3.connect(DATA/'research.sqlite3',timeout=5);conn.row_factory=sqlite3.Row
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,url TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,sha256 TEXT NOT NULL,at REAL NOT NULL,original_characters INTEGER NOT NULL,UNIQUE(url,sha256));
    CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,at REAL NOT NULL,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,owner TEXT NOT NULL,url TEXT NOT NULL,kind TEXT NOT NULL,at REAL NOT NULL,status TEXT NOT NULL);
    ''')
    conn.commit();return conn


def cached(key):
    conn=connect()
    try:
        row=conn.execute('SELECT * FROM cache WHERE id=?',(key,)).fetchone()
        return json.loads(row['value']) if row and time.time()-row['at']<86400 else None
    finally:conn.close()


def cache(key,value):
    conn=connect()
    try:
        conn.execute('DELETE FROM cache WHERE at<?',(time.time()-86400,))
        if conn.execute('SELECT COUNT(*) FROM cache').fetchone()[0]>=300:return
        conn.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(key,time.time(),json.dumps(value,ensure_ascii=False)));conn.commit()
    finally:conn.close()


def reserve(owner,url,kind,host_delay=0):
    conn=connect()
    try:
        conn.execute('BEGIN IMMEDIATE');now=time.time()
        if conn.execute('SELECT COUNT(*) FROM requests WHERE at>?',(now-60,)).fetchone()[0]>=6:raise ResearchError('公开资料请求达到每分钟6次保护线，请稍后操作。')
        if conn.execute('SELECT COUNT(*) FROM requests WHERE at>?',(now-86400,)).fetchone()[0]>=60:raise ResearchError('公开资料请求达到24小时60次上限。')
        if owner!='manual' and conn.execute('SELECT COUNT(*) FROM requests WHERE owner=?',(owner,)).fetchone()[0]>=20:raise ResearchError('本任务达到20次公开资料请求上限。')
        if host_delay:
            parsed=urlsplit(url);origin='https://'+parsed.netloc+'/%'
            last=conn.execute("SELECT MAX(at) FROM requests WHERE kind='page' AND url LIKE ?",(origin,)).fetchone()[0]
            if last and now-last<host_delay:raise ResearchError('网站要求更长访问间隔，请稍后手动操作。')
        identifier=str(uuid4());conn.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)',(identifier,owner,url,kind,now,'reserved'));conn.commit()
        return identifier
    finally:conn.close()


def mark(identifier,status):
    conn=connect()
    try:conn.execute('UPDATE requests SET status=? WHERE id=?',(status,identifier));conn.commit()
    finally:conn.close()


def _fetch(url,owner,kind,deadline):
    url=normalize(url)
    for _ in range(3):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ResearchError('公开网页读取超过时间上限，未重试。')
        host_delay=check_robots(url,owner,deadline) if kind=='page' else 0
        if deadline<=time.monotonic():raise ResearchError('公开网页读取超过时间上限，已停止。')
        identifier=reserve(owner,url,kind,host_delay)
        env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','SYSTEMROOT'}}
        try:
            result=subprocess.run([sys.executable,'-m','app.public_http'],input=json.dumps({'url':url,'timeout':min(8,remaining)}).encode(),capture_output=True,
                                  timeout=max(0.5,min(18,deadline-time.monotonic())),cwd=ROOT/'apps/api',env=env)
            value=json.loads(result.stdout)
        except (subprocess.TimeoutExpired,ValueError,UnicodeError):
            mark(identifier,'failed');raise ResearchError('公开资料连接超时或失败，未重试或切换服务。') from None
        if value.get('error'):
            mark(identifier,'blocked');raise ResearchError('公开资料读取被阻止：'+value['error'])
        mark(identifier,str(value['status']))
        if value['status'] in {301,302,303,307,308}:
            url=normalize(urljoin(url,value.get('location','')));continue
        return value
    raise ResearchError('公开页面重定向超过上限，已停止。')


def check_robots(url,owner,deadline):
    parsed=urlsplit(url);origin='https://'+parsed.netloc;key='robots:'+origin
    value=cached(key)
    if value is None:
        response=_fetch(origin+'/robots.txt',owner,'robots',deadline)
        if response['status'] in {404,410}:value={'allow':True}
        elif response['status']==200:
            value={'text':base64.b64decode(response.get('body','')).decode('utf-8',errors='replace')}
        else:raise ResearchError('网站未允许读取robots规则，已停止。')
        cache(key,value)
    if value.get('allow'):return 0
    robot=RobotFileParser();robot.parse(value.get('text','').splitlines())
    if not robot.can_fetch('ProductArchitectAgent',url):raise ResearchError('网站robots规则不允许当前页面读取。')
    delay=robot.crawl_delay('ProductArchitectAgent') or 0
    rate=robot.request_rate('ProductArchitectAgent')
    return max(delay,rate.seconds/rate.requests if rate and rate.requests>0 else 0)


class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.skip=0;self.title_depth=0;self.title=[];self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','noscript','nav','footer','header','svg','form'}:self.skip+=1
        if tag=='title':self.title_depth+=1
        if not self.skip and tag in {'p','div','br','li','h1','h2','h3','pre','tr'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style','noscript','nav','footer','header','svg','form'}:self.skip=max(0,self.skip-1)
        if tag=='title':self.title_depth=max(0,self.title_depth-1)
    def handle_data(self,value):
        if self.title_depth:self.title.append(value)
        if not self.skip:self.parts.append(value)


def plain(value):
    parser=Text();parser.feed(value)
    content=re.sub(r'[ \t\r\f\v]+',' ',''.join(parser.parts))
    return ''.join(parser.title).strip()[:160],re.sub(r'\n\s*\n+','\n\n',content).strip()


def search(query,provider='docs',owner='manual'):
    if owner!='manual':
        state=require_run(owner,True)
        original=(state.get('change_request') or state.get('user_request') or '').strip()
        if len(original)>=16 and original in query:raise ResearchError('只允许检索必要关键词，不发送需求全文。')
    if not query.strip() or len(query)>200 or re.search(r'\bsk-[A-Za-z0-9_-]{12,}',query):raise ResearchError('请输入200字以内的公开检索词，不包含密钥。')
    key='search:'+provider+':'+hashlib.sha256(query.encode()).hexdigest();hit=cached(key)
    if hit is not None:return {**hit,'cached':True}
    if provider=='docs':
        terms=re.findall(r'[a-z0-9_]+',query.lower())
        for group in re.findall(r'[\u3400-\u9fff]+',query):terms.extend(group[i:i+2] for i in range(max(1,len(group)-1)))
        ranked=sorted(((sum(term in (title+' '+words).lower() for term in terms),title,url,words) for title,url,words in DOCS),reverse=True)
        items=[{'title':title,'url':url,'snippet':words,'read':False} for score,title,url,words in ranked if score>0][:5]
    else:
        if not _gate.acquire(blocking=False):raise ResearchError('公开资料工具繁忙，请稍后再试。')
        try:
            if provider=='wikipedia':url='https://zh.wikipedia.org/w/api.php?'+urlencode({'action':'query','list':'search','srsearch':query,'format':'json','srlimit':5})
            elif provider=='github':url='https://api.github.com/search/repositories?'+urlencode({'q':query+' is:public','per_page':5})
            else:raise ResearchError('未启用该搜索服务；不自动接入付费供应商。')
            response=_fetch(url,owner,'search',time.monotonic()+25)
            if response['status']!=200:raise ResearchError('免费公开检索被拒绝或限流（HTTP '+str(response['status'])+'），未重试。')
            value=json.loads(base64.b64decode(response['body']))
            if provider=='wikipedia':items=[{'title':item['title'],'url':'https://zh.wikipedia.org/wiki/'+quote(item['title'],safe=''),'snippet':plain(item.get('snippet',''))[1][:500],'read':False} for item in value.get('query',{}).get('search',[])[:5]]
            else:items=[{'title':item['full_name'],'url':normalize(item['html_url']),'snippet':(item.get('description') or '')[:500],'read':False} for item in value.get('items',[])[:5]]
        except (KeyError,ValueError,UnicodeError):raise ResearchError('公开搜索返回了无法解析的数据。') from None
        finally:_gate.release()
    result={'provider':provider,'query':query,'items':items,'cached':False,'notice':'结果是候选链接/摘要，尚未读取页面，不代表完整网页内容。'}
    cache(key,result);return result


def read(url,owner='manual'):
    url=normalize(url);key='page:'+hashlib.sha256(url.encode()).hexdigest();hit=cached(key)
    if hit is not None:return {**hit,'cached':True}
    if not _gate.acquire(blocking=False):raise ResearchError('网页读取繁忙，请稍后操作。')
    try:
        response=_fetch(url,owner,'page',time.monotonic()+35)
        if response['status']!=200:raise ResearchError('公开网页未返回可读内容（HTTP '+str(response['status'])+'）；不登录或绕过限制。')
        mime=response.get('content_type','').split(';')[0].strip().lower()
        if mime not in {'text/html','text/plain','text/markdown','application/xhtml+xml'}:raise ResearchError('只读取公开HTML或文本，不自动下载附件或执行脚本。')
        match=re.search(r'charset=[\x22\x27]?([a-zA-Z0-9_-]+)',response.get('content_type',''),re.I)
        encoding=match.group(1).lower() if match else 'utf-8'
        if encoding not in {'utf-8','utf8','gbk','gb18030','iso-8859-1'}:encoding='utf-8'
        text=base64.b64decode(response.get('body','')).decode(encoding,errors='replace')
        if mime in {'text/html','application/xhtml+xml'} and re.search(r'<input[^>]*type\s*=\s*[\x22\x27]?password',text,re.I):raise ResearchError('页面包含登录表单，当前只读工具不处理。')
        title,content=plain(text) if mime in {'text/html','application/xhtml+xml'} else ('',text.strip())
        if not content:raise ResearchError('未提取到正文；动态或登录页面不自动用浏览器绕过。')
        full_length=len(content);content=content[:TEXT_LIMIT]
        value={'id':str(uuid4()),'url':response['url'],'title':title or urlsplit(response['url']).hostname,'content':content,
               'sha256':hashlib.sha256(content.encode()).hexdigest(),'at':time.time(),'original_characters':full_length}
        conn=connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute('SELECT * FROM sources WHERE url=? AND sha256=?',(value['url'],value['sha256'])).fetchone()
            if existing:value=dict(existing)
            else:
                total=conn.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(content AS BLOB))),0) FROM sources').fetchone()
                if total[0]>=500 or total[1]+len(content.encode())>32*1024*1024:raise ResearchError('公开网页快照达到500份或32MiB上限，请先整理来源。')
                conn.execute('INSERT INTO sources VALUES (:id,:url,:title,:content,:sha256,:at,:original_characters)',value)
            conn.commit()
        finally:conn.close()
        value['cached']=False;cache(key,value);return value
    except (KeyError,ValueError,UnicodeError):raise ResearchError('页面地址或内容无效，未重试。') from None
    finally:_gate.release()


def freeze(ids,previous=None):
    if len(ids)>MAX_SELECTED or len(set(ids))!=len(ids):raise ResearchError('最多选择8个不同的公开网页快照。')
    conn=connect();remaining=CONTEXT_LIMIT;items=[];old={item['id']:item for item in previous or []}
    try:
        for identifier in ids:
            identifier=str(UUID(str(identifier)))
            row=old.get(identifier) or conn.execute('SELECT * FROM sources WHERE id=?',(identifier,)).fetchone()
            if not row:raise ResearchError('网页来源已删除，请重新选择。')
            value=dict(row);value['content']=value['content'][:remaining];remaining-=len(value['content'])
            value['omitted_characters']=value['original_characters']-len(value['content']);items.append(value)
        return items
    finally:conn.close()


def summaries(items):return [{key:value for key,value in item.items() if key!='content'} for item in items]


def require_run(run_id,network=False):
    from .store import get_run
    state=get_run(run_id) or {}
    if network and not state.get('research_enabled'):raise ResearchError('本任务未允许联网研究，已拒绝远程请求。')
    return state


class SearchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=200)
    provider:Literal['docs','wikipedia','github']='docs'


class ReadInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    url:str=Field(min_length=1,max_length=2048)


@router.get('/capabilities')
def capabilities():return {'enabled':True,'default_task_enabled':False,'providers':['docs','wikipedia','github'],'general_web_search':False,'max_selected':MAX_SELECTED,'mcp':MCP_EXTENSION,'paid_api':False}


@router.post('/search')
def search_api(payload:SearchInput):
    try:return search(payload.query,payload.provider)
    except (ResearchError,ValueError) as error:raise HTTPException(409,str(error)) from None


@router.post('/read')
def read_api(payload:ReadInput):
    try:return read(payload.url)
    except (ResearchError,ValueError) as error:raise HTTPException(409,str(error)) from None


@router.get('/sources')
def list_sources():
    conn=connect()
    try:return {'items':summaries([dict(row) for row in conn.execute('SELECT * FROM sources ORDER BY at DESC LIMIT 100')])}
    finally:conn.close()


@router.get('/sources/{source_id}')
def read_source(source_id:UUID):
    try:return freeze([str(source_id)])[0]
    except ResearchError as error:raise HTTPException(404,str(error)) from None


def run_snapshot(run_id,base=None):
    conn=connect()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS run_sources(run_id TEXT NOT NULL,source_id TEXT NOT NULL,value_json TEXT NOT NULL,at REAL NOT NULL,PRIMARY KEY(run_id,source_id))');conn.commit()
        rows=conn.execute('SELECT value_json FROM run_sources WHERE run_id=? ORDER BY at,source_id',(run_id,)).fetchall()
        result=list(base or []);seen={item['id'] for item in result};remaining=max(0,CONTEXT_LIMIT-sum(len(item.get('content','')) for item in result))
        for row in rows:
            item=json.loads(row[0])
            if item['id'] in seen:continue
            item['content']=item['content'][:remaining];remaining-=len(item['content']);result.append(item);seen.add(item['id'])
        return result[:MAX_SELECTED]
    finally:conn.close()


def read_for_run(run_id,url):
    state=require_run(run_id,True);items=run_snapshot(run_id,state.get('web_snapshot',[]));url=normalize(url)
    previous=next((item for item in items if item['url']==url),None)
    if previous:return {**previous,'cached':True}
    remaining=CONTEXT_LIMIT-sum(len(item.get('content','')) for item in items)
    if len(items)>=MAX_SELECTED or remaining<=0:raise ResearchError('本任务公开资料已达到8份或24000字符上限。')
    page=read(url,run_id);page['content']=page['content'][:remaining];page['omitted_characters']=page['original_characters']-len(page['content'])
    conn=connect()
    try:
        if conn.execute('SELECT COUNT(*) FROM run_sources').fetchone()[0]>=5000:raise ResearchError('研究引用记录达到上限，请先整理项目。')
        conn.execute('INSERT OR IGNORE INTO run_sources VALUES (?,?,?,?)',(run_id,page['id'],json.dumps(page,ensure_ascii=False),time.time()));conn.commit()
    finally:conn.close()
    return page


@router.delete('/sources/{source_id}',status_code=204)
def delete_source(source_id:UUID):
    conn=connect()
    try:
        conn.execute('DELETE FROM cache WHERE json_extract(value,\'$.id\')=?',(str(source_id),))
        conn.execute('DELETE FROM sources WHERE id=?',(str(source_id),));conn.commit()
    finally:conn.close()



class TaskPublicReader:
    """Read-only provider port that future MCP adapters must reuse, with task authorization."""
    def __init__(self,run_id,provider='docs'):self.run_id=str(UUID(str(run_id)));self.provider=provider
    def search(self,query):
        require_run(self.run_id,True)
        return search(query,self.provider,self.run_id)
    def read(self,url):return read_for_run(self.run_id,url)


class MCPReadOnlyPort(Protocol):
    """Reserved integration contract; no MCP session/server is connected by this module."""
    def search(self,query:str)->dict:...
    def read(self,url:str)->dict:...
