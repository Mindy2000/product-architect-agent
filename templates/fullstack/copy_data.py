"""Explicit consistent copy of a stopped parent's data. Fixed container paths only."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
from backend.file_store import FileStore


def copy():
    source=Path('/parentdata');target=Path('/appdata')
    if source.is_symlink() or target.is_symlink():raise RuntimeError('数据目录异常')
    for name in ('app.sqlite3','files.sqlite3','uploads'):
        path=target/name
        if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):raise RuntimeError('目标已有数据，未覆盖；请先备份或使用空副本')
    app=source/'app.sqlite3'
    if app.is_symlink() or not app.is_file() or app.stat().st_size>128*1024*1024:raise RuntimeError('父数据库无效或超限')
    store=FileStore(source);info=store.stats()
    if info['pending_count']:raise RuntimeError('父版本存在未完成上传，请先显式整理')
    attachments=[]
    for offset in range(0,info['file_count'],100):
        for row in store.list(offset,100)['items']:
            path,metadata=store.download(row['id']);attachments.append((path,metadata))
    directory=source/'uploads'
    if directory.exists() and {p.name for p in directory.iterdir()}!={row['id']+'.blob' for _,row in attachments}:raise RuntimeError('父版本有临时或未索引文件，请先整理')
    stage=target/('.clone-'+uuid.uuid4().hex);stage.mkdir(mode=0o700);published=[]
    try:
        for name in ('app.sqlite3','files.sqlite3'):
            old=source/name
            if old.exists():
                if old.is_symlink():raise RuntimeError('父数据库路径异常')
                with closing(sqlite3.connect(old.as_uri()+'?mode=ro',uri=True)) as conn,closing(sqlite3.connect(stage/name)) as out:conn.backup(out)
                (stage/name).chmod(0o666)
        if attachments:
            (stage/'uploads').mkdir(mode=0o777);(stage/'uploads').chmod(0o777)
            for path,row in attachments:
                dest=stage/'uploads'/path.name;shutil.copyfile(path,dest);dest.chmod(0o666)
        # Child preview and host mutations are stopped by the caller's project locks.
        for name in ('files.sqlite3','uploads','app.sqlite3'):
            path=stage/name
            if not path.exists():continue
            if (target/name).exists():
                if (target/name).is_dir() and not any((target/name).iterdir()):(target/name).rmdir()
                else:raise RuntimeError('目标数据变化，停止复制')
            os.rename(path,target/name);published.append(target/name)
    except BaseException:
        for path in reversed(published):
            if path.is_dir():shutil.rmtree(path)
            else:path.unlink(missing_ok=True)
        raise
    finally:shutil.rmtree(stage,ignore_errors=True)


if __name__=='__main__':copy()
