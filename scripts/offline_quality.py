"""Repeatable zero-provider acceptance in a source-only disposable workspace."""
import argparse,json,os,subprocess,sys,tempfile,shutil,time,socket
from pathlib import Path
import distribution
ROOT=Path(__file__).resolve().parents[1]
def run():
 p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/offline-quality');p.add_argument('--skip-workbench',action='store_true');p.add_argument('--scenario',choices=['ui_workflow','checkpoint_restart','fullstack_roundtrip','workbench_roundtrip','regression_roundtrip']);args=p.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
 for port in (8000,5173):
  with socket.socket() as probe:
   if probe.connect_ex(('127.0.0.1',port))==0:raise SystemExit(f'Port {port} is occupied; stop your workbench before running disposable acceptance.')
 # Model credentials and CI tokens never reach the synthetic API process.
 env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ['API_KEY','TOKEN','PASSWORD','SECRET'])}
 env.update(MODEL_CALLS_ENABLED='false',VISION_CALLS_ENABLED='false',PYTHONUTF8='1')
 results=[]
 with tempfile.TemporaryDirectory(prefix='pa-offline-',dir=ROOT/'data' if (ROOT/'data').exists() else ROOT) as temporary:
  temp=Path(temporary);package=distribution.build(ROOT,temp/'package',kind='source');source=temp/'source';distribution.extract(package,source)
  shutil.copytree(ROOT/'apps/web/dist',source/'apps/web/dist');shutil.copyfile(source/'.env.example',source/'.env');(source/'data').mkdir();(source/'outputs').mkdir()
  scenarios=[('ui_workflow',['--source',str(source),'--report',str(out/'workflow.json')]),('checkpoint_restart',[]),('fullstack_roundtrip',[])]
  if not args.skip_workbench:scenarios.append(('workbench_roundtrip',['--source',str(source),'--report',str(out/'workbench.json')]))
  scenarios.append(('regression_roundtrip',[]))
  for name,options in scenarios:
   if args.scenario and args.scenario!=name:continue
   started=time.monotonic();log=out/(name+'.log')
   with log.open('wb') as stream:child=subprocess.run([sys.executable,str(source/'tests/integration'/(name+'.py')),*options],cwd=source,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=900)
   for evidence in (source/'outputs').glob('offline-*'):
    if evidence.is_file() and evidence.suffix in {'.json','.png'}:shutil.copyfile(evidence,out/evidence.name)
   entry={'scenario':name,'passed':child.returncode==0,'seconds':round(time.monotonic()-started,2)};results.append(entry)
   (out/'report.json').write_text(json.dumps({'provider':'deterministic_offline','provider_calls':0,'checks':results,'passed':all(r['passed'] for r in results)},indent=2))
   print(json.dumps(entry),flush=True)
   if child.returncode:raise SystemExit(child.returncode)
if __name__=='__main__':run()
