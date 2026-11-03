"""Bounded archive inspection; never extract arbitrary ZIP paths."""
import hashlib,io,json,re,stat,zipfile,uuid,time
from pathlib import Path
from . import runner,project
MAX_BYTES=64*1024*1024
ROOT=runner.ROOT/'data'/'recovery'


def inspect(raw:bytes):
    if len(raw)>MAX_BYTES:raise ValueError('备份包超过64MiB。')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist()
        if len(infos)>20000 or len({info.filename for info in infos})!=len(infos):raise ValueError('归档条目过多或重复。')
        if sum(info.file_size for info in infos)>MAX_BYTES:raise ValueError('备份展开内容超过64MiB。')
        for info in infos:
            if info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16):raise ValueError('不支持加密或链接归档。')
            if info.filename.startswith('/') or '\\' in info.filename or '..' in Path(info.filename).parts:raise ValueError('归档包含非法路径。')
        manifest=json.loads(archive.read('SHA256SUMS.json'))
        if manifest.get('format_version')!=1:raise ValueError('不支持该备份格式版本。')
        expected={entry['path']:entry for entry in manifest['files']}
        if len(expected)!=len(manifest['files']) or set(expected)|{'SHA256SUMS.json'}!={info.filename for info in infos}:raise ValueError('归档与清单文件不一致。')
        values={}
        for name,entry in expected.items():
            value=archive.read(name)
            if len(value)!=entry['bytes'] or hashlib.sha256(value).hexdigest()!=entry['sha256']:raise ValueError('备份校验失败：'+name)
            values[name]=value
        candidates={};specs={}
        for name,value in values.items():
            match=re.fullmatch(r'versions/([0-9a-f-]{36})/git/([0-9a-f]{40})/(.+)',name)
            if match and project.managed_path(match[3]):
                key=(str(uuid.UUID(match[1])),match[2]);candidates.setdefault(key,{})[match[3]]=value.decode('utf-8')
            match=re.fullmatch(r'versions/([0-9a-f-]{36})/[^/]+-product-spec.json',name)
            if match:specs[str(uuid.UUID(match[1]))]=json.loads(value).get('spec')
        result=[]
        for (run_id,commit),sources in sorted(candidates.items()):
            project.validate_sources(sources)
            spec=specs.get(run_id)
            if spec:result.append({'run_id':run_id,'commit':commit,'files':sources,'spec':spec})
        if not result:raise ValueError('备份内没有同时具备完整源码和产品方案的可恢复快照。')
        return manifest,result


def path(token):
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    if ROOT.is_symlink():raise ValueError('备份暂存目录异常。')
    result=ROOT/(str(uuid.UUID(token))+'.zip')
    if result.is_symlink():raise ValueError('备份文件异常。')
    return result


def read(token):
    candidate=path(token)
    if not candidate.is_file() or time.time()-candidate.stat().st_mtime>86400:raise ValueError('备份预览已过期，请重新选择文件。')
    if candidate.stat().st_size>MAX_BYTES:raise ValueError('备份文件超限。')
    return inspect(candidate.read_bytes())
