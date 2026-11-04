"""Local onboarding, private workbench backup, and allowlisted diagnostic exports."""
from __future__ import annotations
import asyncio,hashlib,importlib.metadata,json,os,platform,re,shutil,sqlite3,tempfile,time,uuid,zipfile
from contextlib import closing
from pathlib import Path
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from . import runner,workbench_archive as archive,workbench_jobs as jobs
from .process_control import run_bounded
from .security_policy import redact
router=APIRouter(prefix='/api/v1/workbench')

class MaintenanceMiddleware:
 def __init__(self,app):self.app=app
 @property
 def exclusive(self):return bool(jobs.gate.owner)
 async def __call__(self,scope,receive,send):
  if scope['type']!='http':return await self.app(scope,receive,send)
  path=scope['path'];method=scope['method']
  control=path.startswith('/api/v1/workbench/jobs') and (method=='GET' or path.endswith('/cancel'))
  if control:return await self.app(scope,receive,send)
  exclusive=method=='POST' and path in {'/api/v1/workbench/backups/export','/api/v1/workbench/backups/restore'}
  owner='request-'+uuid.uuid4().hex
  if exclusive:
   if not jobs.gate.reserve(owner):return await JSONResponse(status_code=503,content={'detail':'维护操作进行中，请完成后重试。'},headers={'Retry-After':'2'})(scope,receive,send)
   try:
    deadline=time.monotonic()+5
    while jobs.gate.active:
     if time.monotonic()>deadline:return await JSONResponse(status_code=409,content={'detail':'仍有本地操作未结束，请稍后再试。'})(scope,receive,send)
     await asyncio.sleep(.05)
    return await self.app(scope,receive,send)
   finally:jobs.gate.release(owner)
  if not jobs.gate.enter():return await JSONResponse(status_code=503,content={'detail':'维护操作进行中，可继续查看进度或取消。'},headers={'Retry-After':'2'})(scope,receive,send)
  try:return await self.app(scope,receive,send)
  finally:jobs.gate.leave()

class Contract(BaseModel):model_config=ConfigDict(extra='forbid')
class Confirmation(Contract):token:uuid.UUID;confirmed:bool=False
class SetupConfig(Contract):
 provider:str=Field(pattern=r'^(deepseek|qwen|dify)$')
 api_key:str=Field(default='',max_length=1024)
 enabled:bool|None=None
 budget_cny:float|None=Field(default=None,ge=.25,le=99,allow_inf_nan=False)


def root():return runner.ROOT

def local_dir(name):
 path=archive.safe_path(root(),'data/workbench'+('/'+name if name else ''));path.mkdir(parents=True,exist_ok=True,mode=0o700);return path

def private_json(path,value):
 if path.is_symlink():raise archive.ArchiveError('本地记录路径异常。')
 temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
 try:
  with temp.open('x',encoding='utf-8') as stream:os.chmod(temp,0o600);json.dump(value,stream,ensure_ascii=False,indent=2)
  os.replace(temp,path)
 finally:temp.unlink(missing_ok=True)

def config_values():
 from dotenv import dotenv_values
 path=archive.safe_path(root(),'.env')
 if path.is_file() and path.stat().st_size>65536:raise archive.ArchiveError('配置文件大小异常。')
 return dict(dotenv_values(path)) if path.is_file() else {}

def local_command(args):
 try:return run_bounded(args,timeout=8).returncode==0
 except Exception:return False

def setup_status():
 values=config_values()
 try:budget=float(values.get('MODEL_BUDGET_CNY') or '99');budget_ok=0<budget<=99
 except (ValueError,TypeError):budget=99;budget_ok=False
 docker=bool(shutil.which('docker'));daemon=docker and local_command(['docker','--context',runner.DOCKER_CONTEXT,'info','--format','{{.ServerVersion}}'])
 checks=[{'id':'python','title':'Python 3.13','ready':platform.python_version_tuple()[:2]==('3','13'),'required':True,'help':'安装说明中的Python版本必须一致。'},
 {'id':'git','title':'本地版本管理','ready':bool(shutil.which('git')),'required':True,'help':'安装Git后重启工作台。'},
 {'id':'docker','title':'容器服务已启动','ready':daemon,'required':True,'help':'macOS使用Colima；Windows请启动Docker Desktop的Linux容器。'},
 {'id':'worker','title':'前端运行镜像','ready':daemon and runner.worker_image_available(),'required':True,'help':'使用安装入口的 --prepare-runtime 选项准备固定镜像；不会调用模型。'},
 {'id':'text_key','title':'文字模型密钥','ready':bool(values.get('MODEL_API_KEY')),'required':True,'help':'在下方填写自己的DeepSeek密钥，仅保存在本机。'},
 {'id':'budget','title':'累计费用上限','ready':budget_ok,'required':True,'help':'累计上限为0.25–99元，已有费用记录不会重置。'},
 {'id':'enabled','title':'文字模型调用许可','ready':str(values.get('MODEL_CALLS_ENABLED','false')).lower()=='true','required':True,'help':'明确允许调用后保存，再重启生效；向导不发起验证请求。'},
 {'id':'backend','title':'业务后端镜像（可选）','ready':daemon and runner.worker_image_available('product-architect-fullstack:1.0'),'required':False,'help':'需要生成FastAPI/SQLite应用时再准备。'},
 {'id':'vision','title':'视觉理解密钥（可选）','ready':bool(values.get('VISION_API_KEY')),'required':False,'help':'不影响文字生成；千问调用另有次数和金额保护。'},
 {'id':'rag','title':'Dify知识库密钥（可选）','ready':bool(values.get('RAG_API_KEY')),'required':False,'help':'核心流程无需RAG；填写不会上传或检索资料。'}]
 receipt=archive.safe_path(root(),'data/workbench/preferences.json');completed=False
 if receipt.is_file():
  try:completed=bool(json.loads(receipt.read_text()).get('setup_completed'))
  except (ValueError,AttributeError):pass
 from .model import _get_settings
 loaded=_get_settings()
 return {'checks':checks,'ready':all(x['ready'] for x in checks if x['required']),'completed':completed,'budget_cny':budget,'disk_text_enabled':str(values.get('MODEL_CALLS_ENABLED','false')).lower()=='true','loaded_text_enabled':loaded['enabled'],'restart_required':loaded['enabled']!=(str(values.get('MODEL_CALLS_ENABLED','false')).lower()=='true') or loaded.get('api_key','')!=(values.get('MODEL_API_KEY') or '') or loaded.get('budget_cny')!=budget,'key_values_hidden':True,'provider_requests':0}

@router.get('/setup')
def setup():return setup_status()

@router.post('/setup/config')
def save_setup(payload:SetupConfig):
 values=config_values();key_name={'deepseek':'MODEL_API_KEY','qwen':'VISION_API_KEY','dify':'RAG_API_KEY'}[payload.provider];updates={}
 if payload.api_key:
  if re.search(r'[\s\x00-\x1f\x7f"\'\\#]',payload.api_key):raise HTTPException(422,'密钥格式不正确，未保存。')
  updates[key_name]=payload.api_key
 if payload.provider!='deepseek' and (payload.enabled is not None or payload.budget_cny is not None):raise HTTPException(422,'此处仅配置可选服务密钥，不自动开启调用或改写其预算。')
 if payload.provider=='deepseek':
  if payload.enabled is not None:
   if payload.enabled and not (payload.api_key or values.get(key_name)):raise HTTPException(422,'启用文字模型前请先填写密钥。')
   updates['MODEL_CALLS_ENABLED']='true' if payload.enabled else 'false'
  if payload.budget_cny is not None:updates['MODEL_BUDGET_CNY']=str(payload.budget_cny)
 if not updates:raise HTTPException(422,'没有需要保存的修改；留空会保留已有密钥。')
 path=archive.safe_path(root(),'.env');content=path.read_text() if path.exists() else (root()/'.env.example').read_text()
 lines=content.splitlines();seen=set();out=[]
 for line in lines:
  name=line.split('=',1)[0].strip()
  if name in updates:
   if name not in seen:out.append(name+'='+updates[name]);seen.add(name)
  else:out.append(line)
 for name,value in updates.items():
  if name not in seen:out.append(name+'='+value)
 temp=path.with_name('.env.setup-'+uuid.uuid4().hex)
 try:
  with temp.open('x',encoding='utf-8') as stream:os.chmod(temp,0o600);stream.write('\n'.join(out)+'\n');stream.flush();os.fsync(stream.fileno())
  os.replace(temp,path)
 finally:temp.unlink(missing_ok=True)
 return {'saved':True,'key_values_hidden':True,'restart_required':True,'provider_requests':0,'message':'已保存到本机配置。请先停止工作台，再重新启动；本次没有调用任何供应商。'}

@router.post('/setup/complete')
def complete_setup():
 status=setup_status()
 if not status['ready'] or status['restart_required']:raise HTTPException(409,'请先完成必要配置并重启，再标记向导完成。')
 private_json(local_dir('')/'preferences.json',{'setup_completed':True,'completed_at':time.time()})
 return {'completed':True,'provider_requests':0}

def ensure_idle():
 archive.ensure_idle(root())
 from . import preview
 with preview.lock:
  if preview.sessions:raise archive.ArchiveError('请先关闭所有交互预览，再创建或恢复工作台备份。')

@router.get('/backups/plan')
def backup_plan():return archive.plan(root())

@router.post('/backups/export')
def export_backup():
 ensure_idle();token=str(uuid.uuid4());path=local_dir('exports')/(token+'.zip')
 archive.create(root(),path)
 # Keep only this download; raw private backup is not an indefinitely retained server cache.
 from starlette.background import BackgroundTask
 return FileResponse(path,media_type='application/zip',filename='product-architect-workbench-backup.zip',background=BackgroundTask(path.unlink,missing_ok=True))

@router.post('/backups/preview')
async def preview_backup(request:Request):
 folder=local_dir('incoming');pending=[p for p in folder.glob('*.zip') if p.is_file() and not p.is_symlink()];existing=sum(p.stat().st_size for p in pending)
 if len(pending)>=10:raise HTTPException(409,'备份预览最多暂存10份，请先移除不再需要的暂存副本。')
 if existing>=archive.LIMIT*2:raise HTTPException(409,'备份预览暂存已满，请先移除不再需要的上传副本。')
 if shutil.disk_usage(folder).free<archive.LIMIT+128*1024**2:raise HTTPException(409,'可用空间不足以暂存工作台备份。')
 token=str(uuid.uuid4());path=folder/(token+'.zip');total=0
 try:
  with path.open('xb') as stream:
   os.chmod(path,0o600)
   async for chunk in request.stream():
    total+=len(chunk)
    if total>archive.LIMIT:raise HTTPException(413,'工作台备份超过512MiB。')
    stream.write(chunk)
  manifest=await asyncio.to_thread(archive.inspect,path,archive.version(root()))
  # Raw paths may contain document names; preview shows only categories and totals.
  return {'token':token,'version':manifest['app_version'],'file_count':len(manifest['files']),'bytes':sum(e['bytes'] for e in manifest['files']),'credentials_included':False,'compatible':True,'compatibility':manifest['compatibility'],'archive_sha256':archive.file_sha(path),'excluded':archive.EXCLUSIONS,'expires_in_seconds':86400,'message':'校验通过。恢复将创建独立工作台副本；原目录保留，新副本密钥为空、模型调用关闭。'}
 except BaseException:path.unlink(missing_ok=True);raise

@router.get('/backups/previews')
def list_backup_previews():
 items=[]
 for path in local_dir('incoming').glob('*.zip'):
  if path.is_symlink() or not path.is_file():continue
  try:token=str(uuid.UUID(path.stem))
  except ValueError:continue
  info=path.stat();items.append({'token':token,'bytes':info.st_size,'expired':time.time()-info.st_mtime>86400})
 return {'items':items,'private_local_only':True}

@router.get('/backups/preview/{token}')
def recheck_backup(token:uuid.UUID):
 path=archive.safe_path(root(),'data/workbench/incoming/'+str(token)+'.zip')
 if not path.is_file() or time.time()-path.stat().st_mtime>86400:raise HTTPException(409,'预览已过期，请移除暂存并重新选择备份。')
 manifest=archive.inspect(path,archive.version(root()))
 return {'token':str(token),'version':manifest['app_version'],'file_count':len(manifest['files']),'bytes':sum(e['bytes'] for e in manifest['files']),'compatible':True,'compatibility':manifest['compatibility'],'archive_sha256':archive.file_sha(path),'credentials_included':False,'message':'校验通过。恢复将创建独立工作台副本；原目录保留，新副本密钥为空、模型调用关闭。'}

@router.delete('/backups/preview/{token}')
def discard_preview(token:uuid.UUID):
 path=archive.safe_path(root(),'data/workbench/incoming/'+str(token)+'.zip');path.unlink(missing_ok=True);return {'removed':True}

@router.post('/backups/restore')
def restore_backup(payload:Confirmation):
 if not payload.confirmed:raise HTTPException(422,'恢复前需要明确确认。')
 ensure_idle();path=archive.safe_path(root(),'data/workbench/incoming/'+str(payload.token)+'.zip')
 if not path.is_file() or time.time()-path.stat().st_mtime>86400:raise HTTPException(409,'预览已过期，请重新选择备份。')
 return archive.restore(root(),path,payload.token)


def diagnostic_report():
 # Positive schema only: never read raw logs, environment dumps, document text, prompts or source.
 try:values=config_values();config_health='readable'
 except archive.ArchiveError:values={};config_health='unreadable'
 counts={};errors={};database_health='readable'
 try:rows=archive.query(root(),'data/runs.sqlite3',"SELECT CASE WHEN json_valid(state) THEN CASE WHEN json_type(state)='object' THEN json_extract(state,'$.status') ELSE '__corrupt__' END ELSE '__corrupt__' END,CASE WHEN json_valid(state) THEN CASE WHEN json_type(state)='object' THEN json_extract(state,'$.stage') ELSE NULL END ELSE NULL END FROM runs")
 except (sqlite3.Error,archive.ArchiveError):rows=[];database_health='unreadable'
 for status,stage in rows:
  if status=='__corrupt__':counts['corrupt']=counts.get('corrupt',0)+1;continue
  if status not in archive.TERMINAL|{'queued','running'}:status='unknown'
  counts[status]=counts.get(status,0)+1
  if status.startswith('failed') or status.startswith('blocked'):
   stage=stage if stage in {'planning','executor','build','browser_test','delivery','repair','requirements','generation','sandbox'} else 'other'
   errors[stage]=errors.get(stage,0)+1
 packages={}
 for name in ['fastapi','langgraph','pydantic','playwright','pypdf','pillow']:
  try:packages[name]=importlib.metadata.version(name)
  except importlib.metadata.PackageNotFoundError:packages[name]='not_installed'
 report={'format':1,'created_at':time.time(),'application_version':archive.version(root()),'system':{'platform':platform.system(),'machine':platform.machine(),'python':platform.python_version()},'packages':packages,'database_health':database_health,'configuration_health':config_health,'installation':{'git_available':bool(shutil.which('git')),'docker_cli_available':bool(shutil.which('docker')),'frontend_built':(root()/'apps/web/dist/index.html').is_file(),'free_disk_gib':round(shutil.disk_usage(root()).free/1024**3,1)},'configuration':{'text_key_present':bool(values.get('MODEL_API_KEY')),'vision_key_present':bool(values.get('VISION_API_KEY')),'rag_key_present':bool(values.get('RAG_API_KEY')),'text_enabled':str(values.get('MODEL_CALLS_ENABLED','false')).lower()=='true'},'runs_by_status':counts,'failure_stages':errors,'included':['系统与依赖版本','本地工具是否存在','配置布尔状态','任务状态与失败阶段计数'],'excluded':['API密钥与.env','用户名、安装路径、账户标识','任务需求与文档正文','源码、业务数据库及附件','原始日志及任意异常文本'],'network_requests':0}
 from .maintenance_errors import CATALOG
 try:
  report['maintenance_failures']=[{'kind':item['kind'] if item['kind'] in {'backup','restore','regression'} else 'unknown','status':item['status'] if item['status'] in jobs.TERMINAL else 'active','error_code':(item.get('result',{}).get('failure') or {}).get('code') if (item.get('result',{}).get('failure') or {}).get('code') in CATALOG else ('service_interrupted' if item['status']=='interrupted' else 'unknown_failure')} for item in jobs.recent(root()) if item['status'] in {'failed','interrupted'}]
 except (sqlite3.Error,ValueError,TypeError,KeyError,AttributeError,archive.ArchiveError):report['maintenance_failures']=[{'status':'unreadable','error_code':'database_invalid'}]

 # Defense in depth, without sending diagnostics anywhere.
 text=redact(json.dumps(report,ensure_ascii=False)).replace(str(root()),'[WORKBENCH]').replace(str(Path.home()),'[HOME]')
 return json.loads(text)

@router.post('/diagnostics/preview')
def preview_diagnostic():
 directory=local_dir('diagnostics')
 for path in directory.glob('*.json'):
  if path.is_symlink() or not path.is_file() or time.time()-path.stat().st_mtime<=600:continue
  try:uuid.UUID(path.stem)
  except ValueError:continue
  path.unlink(missing_ok=True)
 token=str(uuid.uuid4());report=diagnostic_report();private_json(directory/(token+'.json'),report)
 return {'token':token,'report':report,'expires_in_seconds':600,'sent_to_remote':False}

@router.get('/diagnostics/{token}/download')
def download_diagnostic(token:uuid.UUID):
 path=archive.safe_path(root(),'data/workbench/diagnostics/'+str(token)+'.json')
 if not path.is_file() or time.time()-path.stat().st_mtime>600:raise HTTPException(409,'诊断预览过期，请重新预览。')
 target=local_dir('diagnostics')/(uuid.uuid4().hex+'.zip')
 with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('diagnostic.json',path.read_bytes());z.writestr('README.txt','本包由用户预览后手动下载，未自动发送到任何平台。仅包含白名单系统/依赖/配置状态和计数；不包含原始日志、密钥或业务内容。\n')
 target.chmod(0o600)
 from starlette.background import BackgroundTask
 return FileResponse(target,filename='product-architect-diagnostics.zip',media_type='application/zip',background=BackgroundTask(target.unlink,missing_ok=True))


class JobRequest(Contract):
 kind:str=Field(pattern=r'^(backup|restore|regression)$')
 token:uuid.UUID|None=None
 run_id:uuid.UUID|None=None
 confirmed:bool=False
 expected_sha256:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')

@router.get('/jobs')
def maintenance_jobs():return {'items':jobs.recent(root())}

@router.get('/jobs/{identifier}')
def maintenance_job(identifier:uuid.UUID):
 result=jobs.read(root(),identifier)
 if result is None:raise HTTPException(404,'维护任务不存在。')
 return result

@router.post('/jobs/{identifier}/cancel')
def cancel_maintenance_job(identifier:uuid.UUID):return jobs.cancel(root(),identifier)

@router.post('/jobs')
def start_maintenance_job(payload:JobRequest):
 base=root()
 if payload.kind=='restore' and (not payload.token or not payload.confirmed):raise HTTPException(422,'请选择备份并明确确认恢复。')
 if payload.kind=='regression' and not payload.run_id:raise HTTPException(422,'请选择需要回归检查的项目版本。')
 def operation(identifier,progress,cancelled):
  ensure_idle()
  if payload.kind=='backup':
   artifacts=jobs.folder(base)/'artifacts';artifacts.mkdir(exist_ok=True,mode=0o700)
   if sum(p.stat().st_size for p in artifacts.glob('*.zip') if p.is_file() and not p.is_symlink())+archive.LIMIT>archive.LIMIT*2:raise archive.ArchiveError('已完成的维护备份占用较多空间，请先下载并移除旧任务产物。')
   target=artifacts/(identifier+'.zip');manifest=archive.create(base,target,progress,cancelled)
   return {'file_count':len(manifest['files']),'download':'/api/v1/workbench/jobs/'+identifier+'/download'}
  if payload.kind=='restore':
   path=archive.safe_path(base,'data/workbench/incoming/'+str(payload.token)+'.zip')
   if not path.is_file() or time.time()-path.stat().st_mtime>86400:raise archive.ArchiveError('备份预览已过期，请重新选择。')
   if payload.expected_sha256 and archive.file_sha(path)!=payload.expected_sha256:raise archive.ArchiveError('备份已变化，请重新预览确认。')
   return archive.restore(base,path,payload.token,progress,cancelled)
  from .regression import execute_job
  return execute_job(base,str(payload.run_id),identifier,progress,cancelled)
 return jobs.start(base,payload.kind,payload.model_dump(mode='json'),operation)

@router.get('/jobs/{identifier}/download')
def download_job(identifier:uuid.UUID):
 state=maintenance_job(identifier)
 if state['kind']!='backup' or state['status']!='completed':raise HTTPException(409,'备份尚未完成。')
 path=archive.safe_path(root(),'data/workbench/jobs/artifacts/'+str(identifier)+'.zip')
 if not path.is_file():raise HTTPException(410,'此任务的备份产物已移除。')
 return FileResponse(path,filename='product-architect-workbench-backup.zip',media_type='application/zip')

@router.delete('/jobs/{identifier}/artifact')
def discard_job_artifact(identifier:uuid.UUID):
 state=maintenance_job(identifier)
 if state['status'] not in jobs.TERMINAL:raise HTTPException(409,'请先等待任务结束。')
 path=archive.safe_path(root(),'data/workbench/jobs/artifacts/'+str(identifier)+'.zip');path.unlink(missing_ok=True)
 return {'removed':True,'source_data_preserved':True}
