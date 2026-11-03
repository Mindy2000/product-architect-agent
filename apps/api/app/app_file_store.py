"""Trusted local application file store, also copied into generated app runtimes."""
from contextlib import closing
import codecs
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

MAX_FILE=20*1024*1024
MAX_TOTAL=256*1024*1024
MAX_FILES=500
TYPES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.pdf':'application/pdf','.txt':'text/plain','.md':'text/markdown','.markdown':'text/markdown'}


class FileStoreError(Exception): pass


class FileStore:
    def __init__(self,root):
        self.root=Path(root)
        if self.root.is_symlink() or (self.root/'uploads').is_symlink() or (self.root/'files.sqlite3').is_symlink():raise FileStoreError('资料目录异常。')
        self.uploads=self.root/'uploads'
        self.database=self.root/'files.sqlite3'

    def connect(self,readonly=False):
        if self.database.is_symlink() or self.uploads.is_symlink():raise FileStoreError('资料目录异常。')
        if self.database.exists() and self.database.stat().st_size>16*1024*1024:raise FileStoreError('文件索引超过16MiB保护上限。')
        if readonly:
            conn=sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True,timeout=10);conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA trusted_schema=OFF')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():conn.close();raise FileStoreError('文件索引包含不支持结构。')
            return conn
        self.root.mkdir(parents=True,exist_ok=True,mode=0o777)
        if not self.uploads.exists():
            self.uploads.mkdir(mode=0o777);self.uploads.chmod(0o777)
        created=not self.database.exists()
        conn=sqlite3.connect(self.database,timeout=10);conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA trusted_schema=OFF')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():conn.close();raise FileStoreError('文件索引包含不支持结构。')
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT NOT NULL,mime TEXT NOT NULL,bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL)')
        conn.commit()
        if created:self.database.chmod(0o666)
        return conn

    def path(self,identifier,suffix='.blob'):
        if suffix not in {'.blob','.part','.trash'}:raise FileStoreError('未知资料状态。')
        path=self.uploads/(str(uuid.UUID(str(identifier)))+suffix)
        if path.is_symlink():raise FileStoreError('资料文件路径异常。')
        return path

    def begin(self,name):
        if not isinstance(name,str) or not 1<=len(name)<=180 or re.search(r'[/\\\x00-\x1f\x7f]',name) or Path(name).suffix.lower() not in TYPES:
            raise FileStoreError('请选择图片、PDF、TXT或Markdown，文件名不能包含路径或控制字符。')
        identifier=str(uuid.uuid4())
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            count,reserved=conn.execute("SELECT COUNT(*),COALESCE(SUM(CASE WHEN status='uploading' THEN ? ELSE bytes END),0) FROM files",(MAX_FILE,)).fetchone()
            temporary=sum(path.stat().st_size for path in self.uploads.iterdir() if path.is_file() and not path.is_symlink() and path.suffix in {'.trash','.blob','.part'})
            reserved=conn.execute("SELECT COUNT(*)*? FROM files WHERE status='uploading'",(MAX_FILE,)).fetchone()[0]
            if count>=MAX_FILES or reserved+temporary+MAX_FILE>MAX_TOTAL:raise FileStoreError('本地文件达到500份或256MiB保护上限，请先整理。')
            conn.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?)',(identifier,name,TYPES[Path(name).suffix.lower()],0,'','uploading',time.time()))
        return identifier

    def finish(self,identifier):
        path=self.path(identifier,'.part')
        size=path.stat().st_size
        if not 0<size<=MAX_FILE:raise FileStoreError('文件为空或超过20MiB。')
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM files WHERE id=? AND status='uploading'",(identifier,)).fetchone()
            if not row:raise FileStoreError('上传记录已变化。')
            digest=hashlib.sha256();decoder=codecs.getincrementaldecoder('utf-8-sig')() if row['mime'].startswith('text/') else None
            with path.open('rb') as stream:
                prefix=stream.read(16);stream.seek(0)
                if row['mime']=='application/pdf' and not prefix.startswith(b'%PDF-'):raise FileStoreError('文件内容不是PDF。')
                if row['mime']=='image/png' and not prefix.startswith(b'\x89PNG\r\n\x1a\n'):raise FileStoreError('文件内容不是PNG。')
                if row['mime']=='image/jpeg' and not prefix.startswith(b'\xff\xd8\xff'):raise FileStoreError('文件内容不是JPEG。')
                while chunk:=stream.read(256*1024):
                    digest.update(chunk)
                    if decoder:
                        if b'\x00' in chunk:raise FileStoreError('文本包含无效内容。')
                        try:decoder.decode(chunk)
                        except UnicodeError:raise FileStoreError('文本须为UTF-8编码。') from None
                if decoder:
                    try:decoder.decode(b'',final=True)
                    except UnicodeError:raise FileStoreError('文本须为UTF-8编码。') from None
            target=self.path(identifier)
            if target.exists():raise FileStoreError('资料标识已存在。')
            os.replace(path,target);target.chmod(0o666)
            try:conn.execute("UPDATE files SET bytes=?,sha256=?,status='ready' WHERE id=?",(size,digest.hexdigest(),identifier));conn.commit()
            except Exception:os.replace(target,path);raise
        return self.get(identifier)

    def abort(self,identifier):
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT status FROM files WHERE id=?',(identifier,)).fetchone()
            if row and row['status']=='uploading':
                self.path(identifier,'.part').unlink(missing_ok=True)
                self.path(identifier).unlink(missing_ok=True)
                conn.execute("DELETE FROM files WHERE id=? AND status='uploading'",(identifier,))

    def get(self,identifier):
        identifier=str(uuid.UUID(str(identifier)))
        if not self.database.exists():raise FileStoreError('资料不存在。')
        with closing(self.connect(readonly=True)) as conn:row=conn.execute('SELECT * FROM files WHERE id=?',(identifier,)).fetchone()
        if not row:raise FileStoreError('资料不存在。')
        return dict(row)

    def list(self,offset=0,limit=50):
        if not 0<=offset<=MAX_FILES or not 1<=limit<=100:raise FileStoreError('分页超限。')
        if not self.database.exists():return {'items':[],'total':0}
        with closing(self.connect(readonly=True)) as conn:
            rows=conn.execute('SELECT * FROM files ORDER BY created_at DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()
            count=conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        return {'items':[dict(row) for row in rows],'total':count}

    def stats(self):
        if not self.database.exists():used,count,pending=0,0,0
        else:
            with closing(self.connect(readonly=True)) as conn:
                used,count,pending=conn.execute("SELECT COALESCE(SUM(bytes),0),COUNT(*),COALESCE(SUM(status='uploading'),0) FROM files").fetchone()
        return {'file_bytes':used,'file_count':count,'pending_count':pending,'limits':{'file_bytes':MAX_FILE,'total_file_bytes':MAX_TOTAL,'files':MAX_FILES},'automatic_cleanup':False}

    def download(self,identifier):
        row=self.get(identifier);path=self.path(identifier)
        if row['status']!='ready' or not path.is_file() or path.stat().st_size!=row['bytes']:raise FileStoreError('资料未完成或已损坏。')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while chunk:=stream.read(256*1024):digest.update(chunk)
        if digest.hexdigest()!=row['sha256']:raise FileStoreError('资料校验失败。')
        return path,row

    def delete(self,identifier):
        row=self.get(identifier);identifier=row['id']
        if row['status']!='ready':raise FileStoreError('未完成上传需使用临时文件整理入口。')
        path=self.path(identifier);trash=self.path(identifier,'.trash')
        if trash.exists():raise FileStoreError('存在同名待整理文件。')
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            if not path.is_file():raise FileStoreError('原文件缺失，未删除索引。')
            os.replace(path,trash)
            try:conn.execute('DELETE FROM files WHERE id=?',(str(identifier),));conn.commit()
            except Exception:os.replace(trash,path);raise
        try:trash.unlink()
        except OSError:pass  # Explicit cleanup can remove this residual copy later.

    def cleanup_preview(self):
        deadline=time.time()-86400;items=[]
        if self.database.exists():
            with closing(self.connect(readonly=True)) as conn:
                for row in conn.execute("SELECT id FROM files WHERE status='uploading' AND created_at<?",(deadline,)):
                    items.append({'id':row['id'],'kind':'incomplete_upload'})
        if self.uploads.exists():
            for path in self.uploads.iterdir():
                if path.is_symlink() or not path.is_file() or path.suffix not in {'.trash','.part'} or path.stat().st_mtime>=deadline:continue
                try:identifier=str(uuid.UUID(path.stem))
                except ValueError:continue
                items.append({'id':identifier,'kind':'temporary_file','suffix':path.suffix})
        return {'items':items[:1000],'minimum_age_hours':24,'ready_files_included':False}

    def cleanup(self):
        plan=self.cleanup_preview()
        for item in plan['items']:
            if item['kind']=='incomplete_upload':self.abort(item['id'])
            else:self.path(item['id'],item['suffix']).unlink(missing_ok=True)
        return {'removed_entries':len(plan['items'])}
