"""Offline comparison jobs and descriptive history; never starts a real model task."""
import json,os,re,shutil,subprocess,sys,tempfile,time,uuid,hashlib
from pathlib import Path
from fastapi import APIRouter
from . import runner,store,workbench_archive as archive,workbench_jobs as jobs,collaboration
from .process_control import run_bounded
from .platform_support import WindowsJob,kill_process_group
router=APIRouter(prefix='/api/v1/evaluations')

@router.get('')
def summary():
    from .model import budget_status
    rows=[]
    for state in store.list_runs(100):
        cost=budget_status(state['run_id']);rows.append({'run_id':state['run_id'],'name':(state.get('product_spec') or {}).get('product_name','项目'),'model':state.get('model_name','未登记'),'status':state.get('status'),
            'repair_rounds':state.get('repair_round',0),'steps':state.get('team_steps',state.get('executor_turns',0)),'cost':cost,'operator_assisted':bool(state.get('operator_assisted')),
            'review_status':state.get('review_status'),'policy':state.get('review_basis',{}).get('review_policy_sha256'),'template':state.get('template_id')})
    return {'suite':'local-collaboration-v1','cases':['tasks','form','dashboard'],'strategies':['standard-40','bounded-8'],
            'history':rows,'offline_reports':[v for v in jobs.recent(runner.ROOT) if v['kind']=='evaluation'],'saved_reports':collaboration.records('workbench','evaluation')[-20:],
            'notice':'离线固定响应仅验证协作和测试机制；历史项目需求不同，不直接比较模型成功率，费用是本地估算。真实模型评测仍未启用。'}

def execute_job(root,identifier,progress,cancelled):
    # Source-only copy: no user projects, private documents, keys or live service ports.
    from .runtime_limits import worker_slot
    sys.path.insert(0,str(root/'scripts'))
    import distribution
    directory=archive.safe_path(root,'data/workbench/jobs/'+identifier);directory.mkdir(parents=True,exist_ok=True)
    with worker_slot(root),tempfile.TemporaryDirectory(prefix='evaluation-',dir=directory) as temp:
        temp=Path(temp);package=distribution.build(root,temp/'package','source');source=temp/'source';distribution.extract(package,source)
        (source/'data').mkdir();(source/'outputs').mkdir();shutil.copyfile(source/'.env.example',source/'.env')
        env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','SYSTEMROOT','DOCKER_CONFIG'}}
        env.update(MODEL_CALLS_ENABLED='false',VISION_CALLS_ENABLED='false',PYTHONUTF8='1',PYTHONPATH=str(source/'apps/api'),PA_DOCKER_CONTEXT=runner.DOCKER_CONTEXT)
        log=directory/'evaluation.log';started=time.monotonic()
        with log.open('wb') as stream:
            child=subprocess.Popen([sys.executable,'-m','app.evaluation_worker',identifier],cwd=source,env=env,stdout=stream,stderr=stream,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0);job=WindowsJob(child)
            try:
                while child.poll() is None:
                    archive.checkpoint(None,cancelled)
                    if time.monotonic()-started>720 or log.stat().st_size>2*1024**2:raise archive.ArchiveError('离线评测超过时限或日志上限，已停止。')
                    status=source/'outputs/evaluation-progress.json'
                    if status.exists():
                        try:
                            value=json.loads(status.read_text());progress(value['phase'],value['done'],6)
                        except (ValueError,KeyError):pass
                    time.sleep(.25)
                if child.returncode:raise archive.ArchiveError('离线评测未完成，原项目保留。可检查运行环境后重试。')
                result=json.loads((source/'outputs/evaluation-result.json').read_text())
            finally:
                job.close();kill_process_group(child)
                containers=run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'ps','-aq','--filter','label=pa.app=product-architect','--filter','label=pa.maintenance='+identifier],timeout=10).stdout.split()
                containers=[x for x in containers if re.fullmatch(r'[0-9a-f]{12,64}',x)]
                if containers:run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'rm','-f',*containers],timeout=20)
    result['elapsed_seconds']=round(time.monotonic()-started,2)
    result['source_version']=json.loads((root/'package.json').read_text())['version']
    result['suite_digest']=hashlib.sha256((root/'apps/api/app/evaluation_worker.py').read_bytes()+b''.join(p.read_bytes() for p in sorted((root/'templates/capabilities').glob('*.json')))).hexdigest()
    archive.checkpoint(None,cancelled)
    with store.db_connection() as conn:
        from .collaboration import setup,now
        setup(conn);value={**result,'id':identifier,'created_at':now()}
        conn.execute('INSERT INTO collaboration_records VALUES (?,?,?,?,?)',('workbench','evaluation',identifier,1,json.dumps(value,ensure_ascii=False)))
    return result
