"""Disposable source-only fixture: a deliberate browser regression and scoped cancellation."""
import json,os,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'apps/api'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import runner,store,project,regression,workbench_archive as archive
from app.process_control import run_bounded
assert ROOT.name=='source','Only run from the disposable offline quality workspace'
ids=[str(uuid.uuid4()),str(uuid.uuid4())]
original={}
for index,identifier in enumerate(ids):
 state={'run_id':identifier,'status':'completed','acceptance_profile':'generated','acceptance_criteria':['标题保持一致'],'product_spec':{'application_type':'frontend'},'parent_run_id':ids[0] if index else None}
 store.create_run(identifier,'synthetic maintenance comparison',state,'offline')
 runner.initialize_workspace(identifier)
 files={'src/style.css':'body{margin:0}', 'src/App.tsx':"export default function App(){return <h1>"+('原始标题' if index==0 else '故意破坏标题')+"</h1>}",'tests/e2e.spec.ts':"import {test,expect} from '@playwright/test';test('标题不变',async({page})=>{await page.goto('/');await expect(page.getByRole('heading',{name:'原始标题',exact:true})).toBeVisible({timeout:1000})})"}
 files={**{name:runner.BASE_FILES[name] for name in project.REQUIRED_FILES},**files}
 runner.apply_generated_files(identifier,[{'path':k,'content':v} for k,v in files.items()]);project.import_sources(identifier,files);original[identifier]=project.source_digest(project.sources(identifier))
job=str(uuid.uuid4())
try:result=regression.execute_job(ROOT,ids[1],job,lambda *a:None,lambda:False)
except Exception:
 log=ROOT/'data/workbench/jobs'/job/'regression.log'
 if log.is_file():print(log.read_text()[-12000:])
 raise
assert result['baseline']['build']==result['current']['build']=='passed',result
assert result['baseline']['test']=='passed' and result['current']['test']=='failed',result
assert len(result['comparison']['regressions'])==1,result
assert all(project.source_digest(project.sources(i))==original[i] for i in ids)
assert not (ROOT/'data/workbench/jobs'/job/'scratch').exists()
# Cancellation after the worker has started must kill only this maintenance job's containers.
job2=str(uuid.uuid4());started=time.monotonic()
try:regression.execute_job(ROOT,ids[1],job2,lambda *a:None,lambda:time.monotonic()-started>1)
except archive.OperationCancelled:pass
else:raise AssertionError('cancellation ignored')
assert not run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'ps','-aq','--filter','label=pa.maintenance='+job2],timeout=10).stdout.strip()
assert not (ROOT/'data/workbench/jobs'/job2/'scratch').exists()
assert all(project.source_digest(project.sources(i))==original[i] for i in ids)
(ROOT/'outputs').mkdir(exist_ok=True)
report={'passed':True,'model_calls':0,'comparison':result['comparison'],'original_sources_preserved':True,'cancelled_worker_cleaned':True};(ROOT/'outputs/offline-regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
