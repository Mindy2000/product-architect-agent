"""Standard-library-only, allowlisted releases and side-by-side local upgrades."""
from __future__ import annotations
import hashlib,json,os,re,shutil,sqlite3,stat,subprocess,tempfile,uuid,zipfile
from pathlib import Path,PurePosixPath
from contextlib import closing

PRODUCT='product-architect-agent'
FORMAT=1
LIMIT=64*1024*1024
ROOT=Path(__file__).resolve().parents[1]
PUBLIC_DOCS={'SETUP.md','DISTRIBUTION.md'}
ROOT_FILES={'README.md','.env.example','.gitignore','.gitattributes','package.json','package-lock.json','requirements.txt','requirements.lock','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md'}
SOURCE_DIRS={'apps/api/app','apps/web/src','templates','runtime','tests','scripts','.github/workflows'}
WEB_FILES={'apps/web/index.html','apps/web/package.json','apps/web/tsconfig.json','apps/web/vite.config.ts'}
USER_DIRS=('data','outputs','generated-projects')
TERMINAL={'completed','failed_validation','failed','failed_model','failed_sandbox','blocked_budget','blocked_configuration','interrupted','paused','cancelled'}

class DistributionError(Exception):pass

def digest(raw):return hashlib.sha256(raw).hexdigest()

def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',value):raise DistributionError('版本格式应为0.1.0或0.1.0-beta.1。')
    return value

def public_path(name,kind):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or str(p)!=name or '..' in p.parts or '\\' in name or ':' in name or any(ord(c)<32 for c in name):return False
    if any(part.startswith('.') for part in p.parts) and name not in {'.env.example','.gitignore','.gitattributes'} and p.parts[0]!='.github':return False
    if any(part in {'__pycache__','node_modules','.git','data','outputs','generated-projects','.venv'} for part in p.parts):return False
    if any(part.startswith('.env') for part in p.parts):return name=='.env.example'
    if name=='runtime/bootstrap/pip-26.2.2-py3-none-any.whl':return True
    if name in ROOT_FILES|WEB_FILES:return True
    if p.parts[0]=='docs':return len(p.parts)==2 and p.name in PUBLIC_DOCS
    if name in {'安装.command','启动.command','停止.command','配置.command','诊断.command','install.cmd','start.cmd','stop.cmd','config.cmd','doctor.cmd'}:return True
    if name.startswith('apps/web/dist/'):return kind!='source' and p.suffix in {'.html','.js','.css','.svg','.png','.woff2'}
    if name.startswith('scripts/') and (p.name.startswith(('acceptance_','audit_m19','benchmark_','repair_benchmarks','import_knowledge_samples','validate_vision_once'))):return False
    for base in SOURCE_DIRS:
        if name.startswith(base+'/'):
            if p.suffix in {'.py','.ts','.tsx','.css','.json','.cjs','.yaml','.yml','.html','.command','.cmd','.ps1','.nsi','.lock'}:return True
            if p.name=='Dockerfile':return True
    return False

def configured_secrets(root):
    path=root/'.env'
    env_secrets=[v.encode() for k,v in os.environ.items() if re.search(r'(?:API_KEY|SECRET|TOKEN|PASSWORD)$',k) and len(v)>=8]
    if not path.exists():return env_secrets
    if path.is_symlink() or path.stat().st_size>65536:raise DistributionError('本地配置路径或大小异常，停止打包。')
    values=env_secrets
    for line in path.read_text().splitlines():
        match=re.match(r'^(?:export\s+)?([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD))\s*=\s*(.*)$',line)
        if match:
            value=match[2].strip().strip('"\'')
            if len(value)>=8:values.append(value.encode())
    return values

def build(root,output,kind='macos-install',release_version=None):
    root=Path(root).resolve();output=Path(output).resolve()
    if kind not in {'source','macos-install','windows-install'}:raise DistributionError('不支持的发行包类型。')
    current=json.loads((root/'package.json').read_text())['version'];release_version=version(release_version or current)
    if release_version!=current:raise DistributionError('发行版本与package.json不一致，请先统一版本号。')
    secrets=configured_secrets(root);files={}
    candidates=[root/name for name in ROOT_FILES|WEB_FILES]
    candidates += [root/'docs'/name for name in PUBLIC_DOCS]
    for base in sorted(SOURCE_DIRS | ({'apps/web/dist'} if kind!='source' else set())):
        directory=root/base
        if directory.is_symlink():raise DistributionError('源目录不允许符号链接：'+base)
        if directory.exists():
            for folder,dirs,names in os.walk(directory,followlinks=False):
                dirs[:]=[d for d in dirs if d not in {'node_modules','__pycache__','.git'}]
                for d in dirs:
                    if (Path(folder)/d).is_symlink():raise DistributionError('源码中有目录链接。')
                candidates.extend(Path(folder)/name for name in names)
    candidates += [root/name for name in ['安装.command','启动.command','停止.command','配置.command','诊断.command','install.cmd','start.cmd','stop.cmd','config.cmd','doctor.cmd']]
    for path in sorted(set(candidates)):
        name=path.relative_to(root).as_posix()
        if not public_path(name,kind) or not path.exists():continue
        if path.is_symlink() or not path.is_file():raise DistributionError('发行源码含异常路径。')
        raw=path.read_bytes()
        if len(raw)>10*1024*1024:raise DistributionError('发行单文件过大。')
        if any(key in raw for key in secrets):raise DistributionError('发行内容发现本地凭证，已停止；不会输出凭证。')
        files[name]=raw
    if (root/'docs/DISTRIBUTION.md').is_file():files['README.md']=(root/'docs/DISTRIBUTION.md').read_bytes()
    for name in ['package.json','requirements.lock','scripts/manage.py','scripts/distribution.py','.env.example']:
        if name not in files:raise DistributionError('发行包缺少必要文件：'+name)
    for line in files['.env.example'].decode().splitlines():
        if re.match(r'^[A-Z0-9_]*API_KEY\s*=\s*[^\s#]',line):raise DistributionError('示例配置必须使用空白Key。')
    if kind!='source' and 'apps/web/dist/index.html' not in files:raise DistributionError('请先构建前端，再生成安装包。')
    manifest={'product':PRODUCT,'format':FORMAT,'version':release_version,'kind':kind,'platform':{'source':'source','macos-install':'macos','windows-install':'windows'}[kind],'python':'3.13','node_build':'24','files':[{'path':n,'bytes':len(b),'sha256':digest(b),'executable':n.endswith('.command')} for n,b in sorted(files.items())]}
    files['RELEASE_MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    if sum(map(len,files.values()))>LIMIT:raise DistributionError('发行包展开内容超过64MiB。')
    output.mkdir(parents=True,exist_ok=True)
    target=output/f'product-architect-{release_version}-{kind}.zip'
    if target.exists():raise DistributionError('发行文件已存在，请使用新的输出目录或版本。')
    with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=((stat.S_IFREG| (0o755 if name.endswith('.command') else 0o644))<<16);info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,raw)
    inspect(target)
    return target

def inspect(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>LIMIT:raise DistributionError('发行文件无效或超过64MiB。')
    try:
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist();names={i.filename for i in infos}
            if len(infos)>5000 or len(names)!=len(infos) or sum(i.file_size for i in infos)>LIMIT:raise DistributionError('发行条目重复或展开大小超限。')
            if 'RELEASE_MANIFEST.json' not in names or archive.getinfo('RELEASE_MANIFEST.json').file_size>1024*1024:raise DistributionError('缺少发行清单或清单超限。')
            manifest=json.loads(archive.read('RELEASE_MANIFEST.json'))
            if manifest.get('product')!=PRODUCT or manifest.get('format')!=FORMAT or manifest.get('kind') not in {'source','macos-install','windows-install'}:raise DistributionError('发行格式不支持。')
            version(manifest.get('version'));entries={e['path']:e for e in manifest['files']}
            if len(entries)!=len(manifest['files']) or set(entries)|{'RELEASE_MANIFEST.json'}!=names:raise DistributionError('清单和发行文件不一致。')
            for info in infos:
                if info.is_dir() or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16) or info.file_size>10*1024*1024:raise DistributionError('不允许链接、加密或超大条目。')
                if info.filename=='RELEASE_MANIFEST.json':continue
                if not public_path(info.filename,manifest['kind']):raise DistributionError('发行包含非公开路径。')
                raw=archive.read(info);entry=entries[info.filename]
                if len(raw)!=entry['bytes'] or digest(raw)!=entry['sha256']:raise DistributionError('发行哈希校验失败。')
            if not {'package.json','requirements.lock','scripts/manage.py','scripts/distribution.py','.env.example'}<=names:raise DistributionError('发行缺少必要源码。')
            package=json.loads(archive.read('package.json'))
            if package['version']!=manifest['version']:raise DistributionError('版本清单不一致。')
            return manifest
    except (KeyError,ValueError,TypeError,zipfile.BadZipFile,UnicodeError,AttributeError,RecursionError) as error:raise DistributionError('发行文件格式损坏。') from error

def extract(path,destination):
    manifest=inspect(path);destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise DistributionError('目标目录已存在，不覆盖已有文件。')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.pa-install-',dir=destination.parent))
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                target=stage/info.filename;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(info));target.chmod(0o755 if info.filename.endswith('.command') else 0o644)
        os.rename(stage,destination)
    except BaseException:shutil.rmtree(stage,ignore_errors=True);raise
    return manifest

def ensure_idle(root):
    for name in ['api','web']:
        pidfile=root/'data'/(name+'.pid')
        if pidfile.is_symlink():raise DistributionError('进程记录异常。')
        if pidfile.is_file():
            text=pidfile.read_text().strip()
            if text.isdigit():
                if os.name=='nt':
                    import ctypes
                    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                    handle=kernel.OpenProcess(0x1000,False,int(text))
                    if not handle:
                        if ctypes.get_last_error()==87:continue
                        raise DistributionError('无法确认Windows服务已停止，未升级。')
                    kernel.CloseHandle(handle)
                else:
                    try:os.kill(int(text),0)
                    except ProcessLookupError:continue
                    except PermissionError:raise DistributionError('无法确认服务已停止。')
                # Conservative: do not race a live or reused PID during a data snapshot.
                raise DistributionError('请先停止源目录的服务，再进行升级复制。')
    path=root/'data/runs.sqlite3'
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            for (raw,) in conn.execute('SELECT state FROM runs'):
                if json.loads(raw).get('status') not in TERMINAL:raise DistributionError('仍有活动任务，请先暂停或取消。')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='product_designs'").fetchone():
                if conn.execute("SELECT 1 FROM product_designs WHERE json_extract(state,'$.status')='generating' LIMIT 1").fetchone():raise DistributionError('仍有方案正在生成。')
    path=root/'data/knowledge/library.sqlite3'
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            if conn.execute("SELECT 1 FROM documents WHERE status IN ('uploading','queued','parsing','chunking','indexing') LIMIT 1").fetchone():raise DistributionError('仍有文档在处理。')

def user_files(root):
    files=[]
    for base in USER_DIRS:
        start=root/base
        if start.is_symlink():raise DistributionError('用户数据根目录不能是链接。')
        if not start.exists():continue
        for folder,dirs,names in os.walk(start,followlinks=False):
            # Generated node_modules is a trusted image link and is recreated by the worker.
            if base=='generated-projects':dirs[:]=[d for d in dirs if d!='node_modules']
            for d in dirs:
                if (Path(folder)/d).is_symlink():raise DistributionError('数据含目录链接，停止升级。')
            for name in names:
                source=Path(folder)/name
                if base=='generated-projects' and name=='node_modules':continue
                if source.is_symlink():raise DistributionError('数据含文件链接，停止升级。')
                if base=='data' and (name.endswith(('.pid','.log','-wal','-shm','-journal')) or name=='launcher.lock'):continue
                files.append(source)
    env=root/'.env'
    if env.is_symlink():raise DistributionError('配置文件不能是链接。')
    if env.is_file():files.append(env)
    return files

def upgrade(root,package,destination,apply=False):
    root=Path(root).resolve();destination=Path(destination).absolute();manifest=inspect(package)
    if destination==root or root in destination.parents or destination in root.parents:raise DistributionError('新版本必须放在源目录外的独立位置。')
    if destination.exists() or destination.is_symlink():raise DistributionError('升级目标必须是新目录。')
    ensure_idle(root);files=user_files(root);total=sum(p.stat().st_size for p in files)
    ancestor=destination.parent
    while not ancestor.exists():ancestor=ancestor.parent
    if shutil.disk_usage(ancestor).free<total+LIMIT+256*1024*1024:raise DistributionError('空间不足以保留旧版并复制数据，请先整理。')
    report={'version':manifest['version'],'destination':str(destination),'source':str(root),'copy_bytes':total,'source_preserved':True,'applied':False,'credentials_included_in_private_local_copy':(root/'.env').is_file()}
    if not apply:return report
    # Same-volume staging means a failed copy never becomes a published new installation.
    destination.parent.mkdir(parents=True,exist_ok=True);stage=destination.parent/('.pa-upgrade-'+uuid.uuid4().hex)
    try:
        extract(package,stage)
        for source in files:
            relative=source.relative_to(root);target=stage/relative
            for index in range(1,len(relative.parts)):
                parent=stage.joinpath(*relative.parts[:index]);original=root.joinpath(*relative.parts[:index])
                parent.mkdir(exist_ok=True);parent.chmod(stat.S_IMODE(original.stat().st_mode)&0o777)
            if source.suffix in {'.sqlite3','.db'}:
                with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(target)) as new:old.backup(new)
            else:shutil.copyfile(source,target)
            target.chmod(0o600 if source.name=='.env' else stat.S_IMODE(source.stat().st_mode)&0o777)
        receipt={'source':str(root),'destination':str(destination),'version':manifest['version'],'policy':'old_directory_preserved_no_automatic_data_rollback'}
        state=stage/'data';state.mkdir(exist_ok=True,mode=0o700);(state/'upgrade-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));(state/'upgrade-receipt.json').chmod(0o600)
        os.rename(stage,destination)
    except BaseException:shutil.rmtree(stage,ignore_errors=True);raise
    return {**report,'applied':True,'next':'请在新目录运行安装入口；旧目录与数据完整保留。'}
