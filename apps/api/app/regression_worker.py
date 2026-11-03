"""Trusted host orchestrator; generated code runs exclusively in the fixed Linux worker."""
import json,os,shutil,sys,uuid
from pathlib import Path
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from . import runner,store,project,regression,container_owner,workbench_archive as archive
ROOT=runner.ROOT

def main():
 identifier=str(uuid.UUID(sys.argv[1]));container_owner.LABELS.extend(['--label','pa.maintenance='+identifier]);directory=archive.safe_path(ROOT,'data/workbench/jobs/'+identifier);options=json.loads((directory/'regression-input.json').read_text());states=[store.get_run(options['parent_run_id']),store.get_run(options['run_id'])];sources=[project.sources(state['run_id']) for state in states]
 lab=directory/'scratch';lab.mkdir(exist_ok=False)
 for name in ['templates','runtime/fullstack']:
  shutil.copytree(ROOT/name,lab/name)
 (lab/'apps/api/app').mkdir(parents=True)
 for name in ['app_file_store.py','crud_schema.py']:shutil.copyfile(ROOT/'apps/api/app'/name,lab/'apps/api/app'/name)
 runner.ROOT=lab;runner.GENERATED_ROOT=lab/'generated-projects';runner.OUTPUT_ROOT=lab/'outputs';store.DATA_DIR=lab/'data';store.RUNS_DB=lab/'data/runs.sqlite3'
 results=[]
 try:
  for index,(state,files) in enumerate(zip(states,sources)):
   def progress(phase):project.atomic_json(directory/'worker-progress.json',{'phase':('基线' if index==0 else '当前版本')+' · '+phase,'done':index,'total':2})
   progress('准备独立源码');rid=str(uuid.uuid4());copy={**state,'run_id':rid,'status':'paused'};store.create_run(rid,'regression fixture',copy,'offline')
   spec=state.get('product_spec') or {};runner.initialize_workspace(rid,application_type=spec.get('application_type','frontend'),crud_schema=spec.get('crud_schema'));runner.apply_generated_files(rid,[{'path':name,'content':value} for name,value in files.items()],replace_all=True)
   profile=state.get('acceptance_profile','generated');workspace=runner.workspace_for(rid)
   config=workspace/'playwright.config.ts';text=config.read_text().replace("reporter:'line'","reporter:[['line'],['json',{outputFile:'test-results/pa-regression.json'}]]");config.write_text(text)
   if profile in regression.PROFILES:runner._write_file(workspace,'tests/acceptance.spec.ts',(ROOT/'benchmarks'/(profile+'.spec.ts')).read_text())
   progress('构建');built=runner.run_phase(rid,'build');result={'build':'passed' if built.exit_code==0 else 'failed','test':'skipped','tests':{},'test_source_digest':project.source_digest({str(p.relative_to(workspace)):p.read_text() for p in (workspace/'tests').rglob('*.ts')})}
   if built.exit_code==0:
    progress('浏览器测试');tested=runner.run_phase(rid,'regression',timeout=180);parsed=regression.test_results(workspace/'test-results/pa-regression.json');result.update(test='passed' if tested.exit_code==0 and parsed else 'failed',tests=parsed,report_available=bool(parsed))
   results.append(result)
  report={'available':True,'baseline':results[0],'current':results[1],'comparison':regression.compare(*results),'source_digests':{'baseline':options['before_digest'],'current':options['after_digest']},'original_sources_preserved':True,'model_calls':0,'profiles':options['profiles']}
  project.atomic_json(directory/'regression-result.json',report)
 finally:shutil.rmtree(lab,ignore_errors=True)
if __name__=='__main__':main()
