"""Local import evidence and baseline-vs-change regression, always in disposable containers."""
import json,os,posixpath,re,shutil,subprocess,sys,time,uuid
from pathlib import Path
from . import runner,store,project,workbench_archive as archive
from .process_control import run_bounded
from .platform_support import WindowsJob,kill_process_group
from fastapi import APIRouter,HTTPException
router=APIRouter(prefix='/api/v1/projects')

PROFILES={'todo-v1','todo-v2','notes-v1','expenses-v1','habits-v1','reading-v1'}
def dependency_graph(files):
 graph={name:[] for name in files};unresolved=[]
 for name,content in files.items():
  for target in re.findall(r'(?:from\s*|import\s*\(?\s*|export\s+[^;\n]*?from\s*)[\'"]([.][^\'"]+)[\'"]',content):
   base=posixpath.normpath(posixpath.join(posixpath.dirname(name),target));bases=[base]
   if base.endswith(('.js','.jsx')):bases.append(posixpath.splitext(base)[0])
   choices=[candidate for value in bases for candidate in [value,value+'.ts',value+'.tsx',value+'.css',value+'/index.ts',value+'/index.tsx'] if candidate in files]
   if choices:graph[name].append(choices[0])
   else:unresolved.append({'file':name,'import':target[:180]})
 return graph,unresolved

def analyze(before,after,mappings=None):
 changed=sorted(name for name in before.keys()|after.keys() if before.get(name)!=after.get(name));graph,unknown=dependency_graph({**before,**after});affected=set(changed)
 for _ in range(128):
  expanded=affected|{name for name,deps in graph.items() if set(deps)&affected}
  if expanded==affected:break
  affected=expanded
 pages=[]
 for route,entries in (mappings or {}).items():pages.append({'route':route,'entries':entries,'affected':bool(set(entries)&affected),'basis':'人工确认的页面源码映射 + 本地相对导入闭包'})
 return {'changed_files':changed,'affected_files':sorted(affected),'pages':pages,'unresolved_imports':unknown,'basis':'有界静态证据，支持相对导入/动态导入/导出；不声称完整行为分析或识别运行时路由。'}

def compare(parent,child):
 old=parent.get('tests',{});new=child.get('tests',{});old_pass={k for k,v in old.items() if v=='passed'};old_fail={k for k,v in old.items() if v=='failed'};new_pass={k for k,v in new.items() if v=='passed'};new_fail={k for k,v in new.items() if v=='failed'}
 return {'regressions':sorted(new_fail&old_pass),'existing_failures':sorted(new_fail&old_fail),'baseline_not_covered':sorted(new_fail-set(old)),'resolved':sorted(old_fail&new_pass),'not_retested':sorted(set(old)-set(new)),'skipped':sorted(k for k,v in new.items() if v=='skipped'),'build_regression':parent.get('build')=='passed' and child.get('build')=='failed','build_existing_failure':parent.get('build')==child.get('build')=='failed','comparable_tests':bool(set(old)&set(new)),'test_source_changed':parent.get('test_source_digest')!=child.get('test_source_digest'),'incomplete':parent.get('build')!='passed' or child.get('build')!='passed' or not old or not new,'note':'只有同名测试在基线通过、当前失败才标为回归；删除或未运行的测试不算修复。修改测试内容时仍需人工核对。'}

def test_results(path):
 if not path.is_file() or path.is_symlink() or path.stat().st_size>2*1024**2:return {}
 data=json.loads(path.read_text());out={}
 def walk(suite,trail):
  for spec in suite.get('specs',[]):
   key='/'.join(trail+[spec.get('title','unknown')]);states=[r.get('status') for test in spec.get('tests',[]) for r in test.get('results',[])]
   out[key]='failed' if any(s in {'failed','timedOut','interrupted'} for s in states) else 'passed' if 'passed' in states else 'skipped'
  for sub in suite.get('suites',[]):walk(sub,trail+[sub.get('title','')])
 for suite in data.get('suites',[]):walk(suite,[suite.get('file') or suite.get('title','tests')])
 return out

def plan(identifier):
 current=store.get_run(identifier)
 if not current or not current.get('parent_run_id'):raise HTTPException(409,'需要选择有父版本的项目。')
 parent=store.get_run(current['parent_run_id'])
 if not parent or current.get('status') not in archive.TERMINAL or parent.get('status') not in archive.TERMINAL:raise HTTPException(409,'请等待父子任务结束后进行回归。')
 before=project.sources(parent['run_id']);after=project.sources(identifier)
 from .maintenance import read_record
 record=read_record(uuid.UUID(identifier));analysis=analyze(before,after,record.get('page_sources',{}))
 links=record.get('links',[]);criteria=current.get('acceptance_criteria',[])
 analysis['criteria_candidates']=[{'index':link['criterion'],'text':criteria[link['criterion']],'basis':'人工追溯关联与受影响文件相交'} for link in links if link['criterion']<len(criteria) and set(link['files'])&set(analysis['affected_files'])]
 return {'run_id':identifier,'parent_run_id':parent['run_id'],'analysis':analysis,'before_digest':project.source_digest(before),'after_digest':project.source_digest(after),'profiles':{'baseline':parent.get('acceptance_profile','generated'),'current':current.get('acceptance_profile','generated')},'mode':'两份独立副本完整构建与测试；结果按测试身份比较，不执行用户原配置脚本','model_calls':0}

@router.get('/{run_id}/regression-plan')
def regression_plan(run_id:uuid.UUID):return plan(str(run_id))
@router.get('/{run_id}/regression-result')
def regression_result(run_id:uuid.UUID):
 if not store.get_run(str(run_id)):raise HTTPException(404,'项目不存在。')
 path=archive.safe_path(runner.ROOT,'data/projects/'+str(run_id)+'/regression-latest.json')
 return json.loads(path.read_text()) if path.is_file() else {'available':False}

def execute_job(root,identifier,job_id,progress,cancelled):
 evidence=plan(identifier);directory=archive.safe_path(root,'data/workbench/jobs/'+job_id);directory.mkdir(parents=True,mode=0o700,exist_ok=True)
 options=directory/'regression-input.json';options.write_text(json.dumps(evidence));options.chmod(0o600);log=directory/'regression.log'
 env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','SYSTEMROOT','DOCKER_CONFIG'}};env.update(MODEL_CALLS_ENABLED='false',VISION_CALLS_ENABLED='false',PYTHONUTF8='1',PYTHONPATH=str(root/'apps/api'),PA_DOCKER_CONTEXT=runner.DOCKER_CONTEXT)
 with log.open('wb') as stream:
  log.chmod(0o600);child=subprocess.Popen([sys.executable,'-m','app.regression_worker',job_id],cwd=root,env=env,stdout=stream,stderr=stream,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0);job=WindowsJob(child);started=time.monotonic()
  try:
   while child.poll() is None:
    archive.checkpoint(None,cancelled)
    if time.monotonic()-started>600 or log.stat().st_size>2*1024**2:raise archive.ArchiveError('回归检查超过时限或输出上限，已停止。','operation_timeout')
    status=directory/'worker-progress.json'
    if status.is_file():
     try:
      state=json.loads(status.read_text());progress(state['phase'],state['done'],state['total'])
     except (ValueError,KeyError):pass
    time.sleep(.2)
   if child.returncode:raise archive.ArchiveError('回归执行未完成；原项目未修改，可检查环境后重试。','runtime_unavailable')
  finally:
   job.close();kill_process_group(child)
   try:
    containers=run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'ps','-aq','--filter','label=pa.app=product-architect','--filter','label=pa.maintenance='+job_id],timeout=10).stdout.split()
    containers=[c for c in containers if re.fullmatch(r'[0-9a-f]{12,64}',c)]
    if containers:run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'rm','-f',*containers],timeout=20,check=True)
   finally:shutil.rmtree(directory/'scratch',ignore_errors=True)
 result=json.loads((directory/'regression-result.json').read_text())
 if project.source_digest(project.sources(identifier))!=evidence['after_digest'] or project.source_digest(project.sources(evidence['parent_run_id']))!=evidence['before_digest']:raise archive.ArchiveError('检查期间源文件有变化，结果未保存，请重新检查。','source_changed')
 project.atomic_json(project.metadata_dir(identifier)/'regression-latest.json',result)
 return result
