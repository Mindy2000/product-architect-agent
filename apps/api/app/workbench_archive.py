"""Bounded private workbench snapshots and side-by-side recovery. Never execute archive code."""
from __future__ import annotations
import hashlib,importlib.util,json,os,re,shutil,sqlite3,stat,tempfile,time,uuid,zipfile,unicodedata
from contextlib import closing
from pathlib import Path,PurePosixPath

LIMIT=512*1024*1024
MAX_FILE=128*1024*1024
MAX_ENTRIES=30000
DATABASES={'runs.sqlite3','checkpoints.sqlite3','tools.sqlite3','api_usage.sqlite3','operations.sqlite3'}
DATA_DIRS={'projects','git','applications','knowledge','dify','research','vision'}
TERMINAL={'completed','failed_validation','failed','failed_model','failed_sandbox','blocked_budget','blocked_configuration','interrupted','paused','cancelled'}
UUID_RE=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
EXCLUSIONS=['.env与API密钥配置','虚拟环境、node_modules、Docker镜像','构建缓存、浏览器测试缓存、OCR语言缓存','临时上传、旧备份与恢复副本','服务PID、原始日志及本机网络/容器配置']
class ArchiveError(Exception):
 def __init__(self,message,code='archive_rejected'):
  super().__init__(message);self.code=code
class OperationCancelled(ArchiveError):pass

def checkpoint(progress=None,cancelled=None,phase='',done=0,total=1):
 if cancelled and cancelled():raise OperationCancelled('操作已取消；原工作台和原备份保留。')
 if progress:progress(phase,done,max(total,1))

from .release_policy import compatibility


def sha(raw):return hashlib.sha256(raw).hexdigest()
def file_sha(path):
 result=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
 return result.hexdigest()
def version(root):return json.loads((root/'package.json').read_text())['version']
def safe_path(root,name):
 p=PurePosixPath(name)
 if not name or p.is_absolute() or str(p)!=name or any(c in name for c in ('\\',':','\x00','<','>','"','|','?','*')) or any(ord(c)<32 for c in name) or '..' in p.parts:raise ArchiveError('备份路径格式异常。')
 if any(part.endswith(('.', ' ')) or re.match(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',part,re.I) for part in p.parts):raise ArchiveError('备份路径包含不兼容的平台文件名。')
 target=root
 for part in p.parts:
  target=target/part
  if target.is_symlink():raise ArchiveError('备份路径含符号链接，已停止。')
 return target

def allowed(name):
 try:safe_path(Path('/__workbench_path_validation__'),name)
 except ArchiveError:return False
 p=PurePosixPath(name);parts=p.parts
 if any(x in {'node_modules','.venv','__pycache__','.env','.git','dist','test-results','playwright-report','hooks'} or x.startswith('.env.') for x in parts):return False
 if parts[-1].endswith(('.pid','.log','-wal','-shm','-journal','.pyc','.lock','.part')):return False
 if name=='settings.json':return True
 if len(parts)==2 and parts[0]=='data':return parts[1] in DATABASES
 if len(parts)>=3 and parts[:2]==('data','workbench'):return name=='data/workbench/preferences.json'
 if len(parts)>=3 and parts[0]=='data' and parts[1] in DATA_DIRS:
  if parts[1] in {'projects','git','applications'} and not re.fullmatch(UUID_RE,parts[2]):return False
  if parts[1]=='git' and (parts[-1] in {'config','alternates','shallow'} or 'info' in parts[3:]):return False
  return True
 if len(parts)>=3 and parts[0]=='generated-projects' and re.fullmatch(UUID_RE,parts[1]):return True
 return len(parts)==2 and parts[0]=='outputs' and re.match(UUID_RE+r'-',parts[1]) is not None

def walk(root, enforce_limits=True):
 selected={}
 bases=[root/'data'/name for name in DATABASES]+[root/'data'/name for name in DATA_DIRS]+[root/'data/workbench/preferences.json',root/'generated-projects',root/'outputs']
 for base in bases:
  safe_path(root,base.relative_to(root).as_posix())
  if not base.exists():continue
  if base.is_file():paths=[base]
  else:
   paths=[]
   for folder,dirs,names in os.walk(base,followlinks=False):
    relative=Path(folder).relative_to(root).parts
    dirs[:]=[d for d in dirs if d not in {'node_modules','.git','dist','__pycache__','test-results','playwright-report','hooks'}]
    if relative==('generated-projects',):dirs[:]=[d for d in dirs if re.fullmatch(UUID_RE,d)]
    for d in dirs:safe_path(root,(Path(folder)/d).relative_to(root).as_posix())
    paths.extend(Path(folder)/n for n in names)
  for path in paths:
   rel=path.relative_to(root).as_posix()
   if not allowed(rel):continue
   safe_path(root,rel)
   info=path.stat()
   if not stat.S_ISREG(info.st_mode) or info.st_nlink>1:raise ArchiveError('备份文件必须是普通独立文件。')
   if enforce_limits and info.st_size>MAX_FILE:raise ArchiveError('备份单文件超过128MiB，请先拆分资料。')
   selected[rel]=(info.st_size,info.st_mtime_ns)
   if len(selected)>100000:raise ArchiveError('文件数量超过预检扫描上限100,000；请分开管理项目资料，未修改原数据。')
 if enforce_limits and (len(selected)>MAX_ENTRIES or sum(v[0] for v in selected.values())>LIMIT):raise ArchiveError('工作台备份超过512MiB或30,000文件上限。')
 return selected

def sqlite_path(root,relative):
 path=safe_path(root,relative)
 for suffix in ('-wal','-shm','-journal'):safe_path(root,relative+suffix)
 return path

def query(root,relative,sql):
 path=sqlite_path(root,relative)
 if not path.is_file():return []
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:return conn.execute(sql).fetchall()

def ensure_idle(root):
 try:
  for raw, in query(root,'data/runs.sqlite3','SELECT state FROM runs'):
   if json.loads(raw).get('status') not in TERMINAL:raise ArchiveError('请先暂停或取消所有开发任务，再创建或恢复工作台备份。')
  path=root/'data/runs.sqlite3'
  if path.exists():
   tables={r[0] for r in query(root,'data/runs.sqlite3',"SELECT name FROM sqlite_master WHERE type='table'")}
   if 'product_designs' in tables:
    for raw, in query(root,'data/runs.sqlite3','SELECT state FROM product_designs'):
     if json.loads(raw).get('status')=='generating':raise ArchiveError('仍有产品方案在生成，请稍后备份。')
  if (root/'data/knowledge/library.sqlite3').exists():
   if query(root,'data/knowledge/library.sqlite3',"SELECT id FROM documents WHERE status IN ('uploading','queued','parsing','chunking','indexing') LIMIT 1"):raise ArchiveError('仍有文档正在处理，请稍后备份。')
 except (sqlite3.Error,ValueError,TypeError) as e:raise ArchiveError('工作台记录无法校验；原资料保留，请先查看诊断。') from e

def settings(root):
 from dotenv import dotenv_values
 env=safe_path(root,'.env');values=dotenv_values(env) if env.is_file() else {}
 permitted={'MODEL_NAME','MODEL_PROVIDER','MODEL_BUDGET_CNY','VISION_MODEL','VISION_PROVIDER','VISION_BUDGET_CNY','VISION_MAX_CALLS_TOTAL','VISION_FREE_ONLY'}
 return {k:v for k,v in values.items() if k in permitted and isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9._-]{1,80}',v)}

def plan(root):
 # Report capacity even when export is blocked; never expose document filenames.
 files=walk(root,enforce_limits=False)
 from .backup_preflight import summarize
 return summarize(root,files,max_bytes=LIMIT,max_file=MAX_FILE,max_entries=MAX_ENTRIES,excluded=EXCLUSIONS)


def create(root,destination,progress=None,cancelled=None):
 checkpoint(progress,cancelled,'准备快照');ensure_idle(root);before=walk(root);entries=[]
 from dotenv import dotenv_values
 env=safe_path(root,'.env');values={**os.environ,**(dict(dotenv_values(env)) if env.is_file() else {})}
 credentials=[str(v).encode() for k,v in values.items() if v and len(str(v))>=8 and re.search(r'(?:API_KEY|SECRET|TOKEN|PASSWORD)$',k,re.I)]
 if shutil.disk_usage(root).free<sum(v[0] for v in before.values())*2+128*1024**2:raise ArchiveError('磁盘空间不足以创建安全备份，请先整理空间。','disk_space')
 destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 created=False
 try:
  with tempfile.TemporaryDirectory(dir=destination.parent) as temp,zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as archive:
   created=True
   total=0
   for index,name in enumerate(sorted(before)):
    checkpoint(progress,cancelled,'备份文件',index,len(before))
    path=safe_path(root,name)
    if path.suffix in {'.sqlite3','.db'}:
     sqlite_path(root,name)
     snapshot=Path(temp)/'snapshot.sqlite3'
     with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(snapshot)) as new:old.backup(new,pages=64,progress=lambda status,left,total:checkpoint(progress,cancelled,'数据库快照',total-left,total))
     with closing(sqlite3.connect(snapshot)) as conn:
      if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ArchiveError('数据库完整性检查未通过。')
     raw=snapshot.read_bytes();snapshot.unlink()
    else:raw=path.read_bytes()
    if len(raw)>MAX_FILE:raise ArchiveError('数据库快照或资料超过单文件上限。')
    if any(key in raw for key in credentials):raise ArchiveError('资料或历史记录含当前已配置的密钥，已停止导出；请先移除资料中的凭证。未修改原数据。')
    total+=len(raw)
    if total>LIMIT-8*1024**2:raise ArchiveError('快照展开大小超过备份上限。')
    archive.writestr(name,raw);entries.append({'path':name,'bytes':len(raw),'sha256':sha(raw)})
   raw=json.dumps(settings(root),ensure_ascii=False).encode();archive.writestr('settings.json',raw);entries.append({'path':'settings.json','bytes':len(raw),'sha256':sha(raw)})
   manifest={'product':'product-architect-workbench','format':1,'app_version':version(root),'created_at':time.time(),'files':entries,'excluded':EXCLUSIONS,'credentials_included':False}
   archive.writestr('WORKBENCH_MANIFEST.json',json.dumps(manifest,ensure_ascii=False))
  if walk(root)!=before:raise ArchiveError('备份期间有文件变化，未生成不一致备份；请停止编辑后重试。')
  inspect(destination,version(root),progress,cancelled);destination.chmod(0o600)
  checkpoint(progress,cancelled,'备份完成',1,1)
  return manifest
 except BaseException:
  if created:destination.unlink(missing_ok=True)
  raise

def inspect(path,expected_version,progress=None,cancelled=None):
 if path.is_symlink() or path.stat().st_size>LIMIT:raise ArchiveError('备份文件异常或超过512MiB。')
 try:
  with zipfile.ZipFile(path) as archive:
   infos=archive.infolist();names={x.filename for x in infos}
   if len(infos)>MAX_ENTRIES+2 or len({unicodedata.normalize('NFC',x).casefold() for x in names})!=len(infos) or len(names)!=len(infos) or sum(x.file_size for x in infos)>LIMIT:raise ArchiveError('备份条目重复或展开大小超限。')
   for info in infos:
    if info.is_dir() or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16) or info.file_size>MAX_FILE:raise ArchiveError('不支持链接、加密、目录或超大条目。')
   if archive.getinfo('WORKBENCH_MANIFEST.json').file_size>8*1024**2:raise ArchiveError('清单过大。')
   manifest=json.loads(archive.read('WORKBENCH_MANIFEST.json'))
   if not isinstance(manifest,dict):raise ArchiveError('备份清单必须是对象。')
   if manifest.get('product')!='product-architect-workbench' or manifest.get('format')!=1 or manifest.get('credentials_included') is not False:raise ArchiveError('备份格式或应用版本不兼容；请使用创建备份时的应用版本。')
   migration=compatibility(manifest.get('app_version'),expected_version)
   if not migration['compatible']:raise ArchiveError('此版本没有已审核的兼容迁移路径，未修改数据；需要确认迁移方案或使用原版本恢复。')
   entries={x['path']:x for x in manifest['files']}
   if 'settings.json' not in entries or len(entries)!=len(manifest['files']) or set(entries)|{'WORKBENCH_MANIFEST.json'}!=names:raise ArchiveError('文件与备份清单不一致。')
   for index,(name,entry) in enumerate(entries.items()):
    checkpoint(progress,cancelled,'校验备份',index,len(entries))
    if not allowed(name):raise ArchiveError('备份包含不支持的路径或凭证配置。')
    raw=archive.read(name)
    if len(raw)!=entry['bytes'] or sha(raw)!=entry['sha256']:raise ArchiveError('备份哈希校验失败。')
   prefs=json.loads(archive.read('settings.json'))
   if not isinstance(prefs,dict) or any(k not in settings_allowed() or not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',v) for k,v in prefs.items()):raise ArchiveError('备份配置不符合白名单。')
   manifest['compatibility']=migration
   return manifest
 except (zipfile.BadZipFile,KeyError,TypeError,ValueError,UnicodeError,RuntimeError) as e:raise ArchiveError('备份文件损坏或清单无效。') from e

def distribution(root):
 spec=importlib.util.spec_from_file_location('pa_backup_distribution',root/'scripts/distribution.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def restore_home(root):return safe_path(root.parent,root.name+'-restored')

def restore(root,archive_path,token,progress=None,cancelled=None):
 manifest=inspect(archive_path,version(root),progress,cancelled);ensure_idle(root)
 home=restore_home(root);home.mkdir(parents=True,exist_ok=True,mode=0o700)
 target=safe_path(root.parent,root.name+'-restored/'+str(uuid.UUID(str(token))))
 if target.exists():
  receipt=safe_path(target,'data/workbench-restore-receipt.json')
  if receipt.is_file() and json.loads(receipt.read_text()).get('archive_sha256')==file_sha(archive_path):return {'location':str(target),'replayed':True,'executed':False,'restart_required':True}
  raise ArchiveError('恢复目标已存在，未覆盖。')
 total=sum(e['bytes'] for e in manifest['files'])
 if shutil.disk_usage(home).free<total+archive_path.stat().st_size+256*1024**2:raise ArchiveError('磁盘不足以保留当前工作台并创建恢复副本。','disk_space')
 stage=Path(tempfile.mkdtemp(prefix='.restore-',dir=home));payload=stage/'installation'
 try:
  checkpoint(progress,cancelled,'准备独立工作台')
  builder=distribution(root)
  try:package=builder.build(root,stage/'source-package',kind='source');builder.extract(package,payload)
  except builder.DistributionError as error:raise ArchiveError(str(error)) from None
  with zipfile.ZipFile(archive_path) as archive:
   for index,entry in enumerate(manifest['files']):
    checkpoint(progress,cancelled,'恢复文件',index,len(manifest['files']))
    if entry['path']=='settings.json':continue
    path=safe_path(payload,entry['path']);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(archive.read(entry['path']));path.chmod(0o600)
   prefs=json.loads(archive.read('settings.json'))
  migration=manifest['compatibility']
  if migration['migration_required']:
   checkpoint(progress,cancelled,'保留迁移前备份')
   old=payload/'data/workbench/migration-original.zip';old.parent.mkdir(parents=True,exist_ok=True)
   with archive_path.open('rb') as src,old.open('xb') as dst:
    for block in iter(lambda:src.read(1024*1024),b''):
     checkpoint(None,cancelled);dst.write(block)
   old.chmod(0o600)
   db=payload/'data/knowledge/library.sqlite3'
   if db.is_file():
    with closing(sqlite3.connect(db)) as conn:
     if conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone() and 'cancel_requested' not in {r[1] for r in conn.execute('PRAGMA table_info(jobs)')}:
      conn.execute('ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0');conn.commit()
  # Never restore executable Git settings, hooks, alternates, or private filesystem bindings.
  for git in (payload/'data/git').glob('*') if (payload/'data/git').exists() else []:
   (git/'config').write_text('[core]\n repositoryformatversion = 0\n bare = false\n[user]\n name = Product Architect Agent\n email = agent@localhost\n')
  for path in payload.rglob('*.sqlite3'):
   with closing(sqlite3.connect(path)) as conn:
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ArchiveError('恢复数据库损坏，已取消副本。')
    # Expired upload cleanup plans must not target anything in the new installation.
    if path.name=='operations.sqlite3' and conn.execute("SELECT 1 FROM sqlite_master WHERE name='cleanup_plans'").fetchone():conn.execute('DELETE FROM cleanup_plans');conn.commit()
  import math
  for key,maximum in [('MODEL_BUDGET_CNY',99),('VISION_BUDGET_CNY',1),('VISION_MAX_CALLS_TOTAL',10000)]:
   if key in prefs:
    try:number=float(prefs[key])
    except (ValueError,TypeError):raise ArchiveError('恢复配置的费用或次数限制无效。') from None
    if not math.isfinite(number) or not 0<number<=maximum:raise ArchiveError('恢复配置的费用或次数超过保护线。')
  default=(payload/'.env.example').read_text()
  for key,value in prefs.items():
   if key not in settings_allowed() or not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',value):raise ArchiveError('恢复配置不符合白名单。')
   default=re.sub(r'^'+key+r'=.*$',key+'='+value,default,flags=re.M)
  default=re.sub(r'^(MODEL_CALLS_ENABLED|VISION_CALLS_ENABLED|QWEN_TEXT_CALLS_ENABLED|QWEN_TEXT_USE_VISION_KEY)=.*$',r'\1=false',default,flags=re.M)
  (payload/'.env').write_text(default);(payload/'.env').chmod(0o600)
  # Container workspaces and business storage require the same shared permissions as fresh projects.
  for base in [payload/'generated-projects',payload/'data/applications']:
   if base.exists():
    base.chmod(0o777)
    for folder,dirs,names in os.walk(base):
     Path(folder).chmod(0o777)
     for name in names:(Path(folder)/name).chmod(0o666)
  receipt=payload/'data/workbench-restore-receipt.json';receipt.parent.mkdir(exist_ok=True)
  receipt.write_text(json.dumps({'format':1,'archive_sha256':file_sha(archive_path),'restored_at':time.time(),'keys_restored':False,'calls_enabled':False,'migration':migration,'pre_migration_archive':'data/workbench/migration-original.zip' if migration['migration_required'] else None}));receipt.chmod(0o600)
  checkpoint(progress,cancelled,'提交独立副本',1,1)
  os.rename(payload,target)
  return {'location':str(target),'replayed':False,'migration':migration,'executed':False,'restart_required':True,'next':'副本已创建。停止当前工作台，在新目录运行安装入口，重新填写自己的密钥后启动；尚未执行生成代码。'}
 finally:shutil.rmtree(stage,ignore_errors=True)

def settings_allowed():return {'MODEL_NAME','MODEL_PROVIDER','MODEL_BUDGET_CNY','VISION_MODEL','VISION_PROVIDER','VISION_BUDGET_CNY','VISION_MAX_CALLS_TOTAL','VISION_FREE_ONLY'}
