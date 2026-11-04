"""No-provider M19 integration: disposable project root, real isolated workers and synthetic data."""
import asyncio,base64,hashlib,io,json,os,shutil,sqlite3,sys,time,uuid,zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'apps/api'))
os.environ['MODEL_CALLS_ENABLED']='false';os.environ['VISION_CALLS_ENABLED']='false'
from app import runner,store,project,fullstack,application_data,library,history,recovery,preview,model,security_policy
from app.process_control import run_bounded
from app.designs import ProductDesign
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPORT=ROOT/'outputs/offline-integration.json'
results={'started_at':time.time(),'provider_calls':0,'cases':[]}

def case(name,operation):
    start=time.monotonic()
    try:
        details=operation();entry={'name':name,'passed':True,'seconds':round(time.monotonic()-start,3),'details':details}
    except Exception as error:
        entry={'name':name,'passed':False,'seconds':round(time.monotonic()-start,3),'error':security_policy.redact(str(error))};results['cases'].append(entry);REPORT.write_text(json.dumps(results,ensure_ascii=False,indent=2));raise
    results['cases'].append(entry);REPORT.write_text(json.dumps(results,ensure_ascii=False,indent=2));print(name,'PASS',flush=True);return details


def expect(value,message):
    if not value:raise AssertionError(message)


def collect_response(response):
    async def read():
        parts=[]
        async for part in response.body_iterator:parts.append(part.encode() if isinstance(part,str) else part)
        return b''.join(parts)
    return asyncio.run(read())


def saved_call_count():
    with sqlite3.connect('file:'+str(ROOT/'data/api_usage.sqlite3')+'?mode=ro',uri=True) as conn:
        return conn.execute('SELECT COUNT(*),COALESCE(SUM(COALESCE(actual_cny,reserve_cny)),0) FROM model_usage').fetchone()


def archive_bytes(entries):
    value=io.BytesIO()
    with zipfile.ZipFile(value,'w',zipfile.ZIP_DEFLATED) as z:
        for name,content in entries.items():z.writestr(name,content)
    return value.getvalue()

SCHEMA={'version':1,'entities':[{'name':'tasks','label':'任务','fields':[{'name':'title','type':'text','required':True},{'name':'done','type':'boolean'}]}]}
APP="""import {useEffect,useState} from 'react'
import {api,type Row} from './pa-crud-client'
export default function App(){const [rows,setRows]=useState<Row[]>([]),[title,setTitle]=useState(''),[error,setError]=useState('');async function load(){try{setRows((await api.list('tasks')).items)}catch(e){setError(String(e))}}useEffect(()=>{void load()},[]);return <main style={{padding:24}}><h1>验收任务管理</h1><label>任务标题<input aria-label="任务标题" value={title} onChange={e=>setTitle(e.target.value)}/></label><button onClick={()=>void api.create('tasks',{title,done:false}).then(()=>{setTitle('');void load()}).catch(e=>setError(String(e)))}>新增任务</button><ul>{rows.map(row=><li key={row.id}>{String(row.title)}<button onClick={()=>void api.remove('tasks',row.id).then(load)}>删除</button></li>)}</ul>{error&&<p role="alert">{error}</p>}</main>}
"""
SUITE="""import {test,expect} from '@playwright/test'
test('真实界面新增与刷新持久化',async({page})=>{await page.goto('/');await expect(page.getByRole('heading',{name:'验收任务管理'})).toBeVisible();await page.getByLabel('任务标题').fill('M19 浏览器任务');await page.getByRole('button',{name:'新增任务'}).click();await expect(page.getByRole('listitem').filter({hasText:'M19 浏览器任务'})).toBeVisible();await page.reload();await expect(page.getByRole('listitem').filter({hasText:'M19 浏览器任务'})).toBeVisible();await page.getByRole('listitem').filter({hasText:'M19 浏览器任务'}).getByRole('button',{name:'删除'}).click();await expect(page.getByText('M19 浏览器任务')).toHaveCount(0)})
test('CRUD 校验与更新删除',async({request})=>{const created=await request.post('/api/data/tasks',{data:{title:'接口验收',done:false}});expect(created.status()).toBe(201);const row=await created.json();const changed=await request.patch('/api/data/tasks/'+row.id,{data:{title:'已更新',done:true}});expect(changed.status()).toBe(200);expect((await changed.json()).title).toBe('已更新');expect((await request.post('/api/data/tasks',{data:{title:'x',unknown:true}})).status()).toBe(422);expect((await request.post('/api/data/tasks',{data:{title:42}})).status()).toBe(422);expect((await request.delete('/api/data/tasks/'+row.id)).status()).toBe(204);expect((await request.get('/api/data/tasks/'+row.id)).status()).toBe(404)})
test('附件上传下载删除',async({request})=>{const uploaded=await request.post('/api/files?filename=acceptance.txt',{data:Buffer.from('M19 synthetic attachment'),headers:{'Content-Type':'application/octet-stream'}});expect(uploaded.status()).toBe(201);const file=await uploaded.json();const downloaded=await request.get('/api/files/'+file.id+'/download');expect((await downloaded.body()).toString()).toBe('M19 synthetic attachment');expect(downloaded.headers()['x-content-type-options']).toBe('nosniff');expect((await request.delete('/api/files/'+file.id)).status()).toBe(204);expect((await request.get('/api/files/'+file.id+'/download')).status()).toBe(404)})
test('拒绝跨站写入与非法文件',async({request})=>{expect((await request.post('/api/data/tasks',{data:{title:'denied'},headers:{Origin:'https://attacker.example'}})).status()).toBe(403);expect((await request.post('/api/files?filename=escape.exe',{data:'x'})).status()).toBe(422)})
"""


def main():
    before=saved_call_count()
    lab=ROOT/'data'/('offline-acceptance-'+uuid.uuid4().hex[:10]);lab.mkdir();results['lab_root']=str(lab)
    for name in ['templates/fullstack','runtime/fullstack']:
        shutil.copytree(ROOT/name,lab/name)
    shutil.copyfile(ROOT/'runtime/preview-worker.cjs',lab/'runtime/preview-worker.cjs')
    for name in ['app_file_store.py','crud_schema.py']:
        target=lab/'apps/api/app'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/'apps/api/app'/name,target)
    identifier=str(uuid.uuid4());spec=ProductDesign.model_validate({'application_type':'fullstack_crud','crud_schema':SCHEMA,'product_name':'M19 合成验收项目','summary':'仅供集中验收使用的合成任务管理样本，不调用模型。','target_users':['验收人员'],'pages':['任务'],'page_details':[{'name':'任务','route':'/','purpose':'验证本地CRUD','sections':['任务列表'],'interactions':['新增','删除']}],'core_features':['任务管理','文件存储'],'assumptions':[],'non_goals':['不调用远程模型'],'acceptance_criteria':['新增刷新后保留','支持修改删除','附件校验完整','备份恢复为独立副本'],'visual_direction':'中文本地简洁界面','data_notes':['SQLite 独立业务数据']}).model_dump()
    with ExitStack() as stack:
        changes=[(runner,'ROOT',lab),(runner,'GENERATED_ROOT',lab/'generated-projects'),(runner,'OUTPUT_ROOT',lab/'outputs'),(store,'DATA_DIR',lab/'data'),(store,'RUNS_DB',lab/'data/runs.sqlite3'),(application_data,'ROOT',lab/'data/application-backups'),(library,'DATA',lab/'data/knowledge'),(recovery,'ROOT',lab/'data/recovery'),(model,'DATA_DIR',lab/'data'),(model,'USAGE_DB',lab/'data/api_usage.sqlite3')]
        for module,name,value in changes:stack.enter_context(patch.object(module,name,value))
        stack.enter_context(patch.object(model,'generate_json',side_effect=AssertionError('Provider calls forbidden in M19')))
        state={'run_id':identifier,'status':'paused','runtime_version':2,'product_spec':spec,'requirements_confirmed':True,'acceptance_criteria':spec['acceptance_criteria'],'trace':[],'repair_round':0,'run_budget_cny':0,'model_call_limit':0,'created_at':'2026-11-03'}
        store.create_run(identifier,'合成验收',state,state['created_at']);workspace=runner.initialize_workspace(identifier,application_type='fullstack_crud',crud_schema=SCHEMA)
        runner.apply_generated_files(identifier,[{'path':'src/App.tsx','content':APP},{'path':'tests/e2e.spec.ts','content':SUITE.split("test('拒绝")[0]}]);runner._write_file(workspace,'tests/acceptance.spec.ts',SUITE)
        def fullstack_build():
            result=runner.run_phase(identifier,'build');expect(result.exit_code==0,result.output);return {'exit_code':result.exit_code,'log':result.output}
        case('fullstack_build',fullstack_build)
        def fullstack_browser():
            result=runner.run_phase(identifier,'acceptance',timeout=180);expect(result.exit_code==0,result.output);return {'exit_code':result.exit_code,'log':result.output,'suite_sha256':hashlib.sha256(SUITE.encode()).hexdigest()}
        case('fullstack_browser_crud_files',fullstack_browser)

        def fixed_health():
            runner._write_file(workspace,'tests/standard/health.spec.ts',(runner.WORKER_CONTEXT.parent/'health.spec.ts').read_text())
            result=runner.run_phase(identifier,'health',timeout=60);expect(result.exit_code==0,result.output);return {'exit_code':result.exit_code,'log':result.output,'product_requirements_verified':False}
        case('fixed_fullstack_page_health',fixed_health)

        def persistent_migration():
            fullstack.data_dir(identifier);result=runner.run_phase(identifier,'migrate');expect(result.exit_code==0,result.output);return json.loads(result.output)
        case('explicit_persistent_migration',persistent_migration)
        def data_roundtrip():
            data=fullstack.data_dir(identifier)
            # Fixture insertion targets only this newly created database after its trusted container migration.
            with sqlite3.connect(data/'app.sqlite3') as conn:conn.execute('INSERT INTO tasks(id,created_at,updated_at,title,done) VALUES (?,?,?,?,?)',(str(uuid.uuid4()),'2026-11-03','2026-11-03','备份中的合成任务',0))
            files=application_data.FileStore(data);fid=files.begin('实验附件.txt');files.path(fid,'.part').write_bytes('仅测试备份恢复'.encode());files.finish(fid)
            token,path=application_data.export_backup(identifier);report=application_data.inspect_backup(path);expect(report['attachment_count']==1,'attachment missing')
            restore_token=str(uuid.uuid4());shutil.copyfile(path,application_data.path_for('previews',restore_token));result=application_data.restore(restore_token,identifier)
            expect(result['status']=='paused' and not result['executed'],'restore must remain paused')
            restored=fullstack.data_dir(result['run_id']);expect((restored/'app.sqlite3').read_bytes()==(data/'app.sqlite3').read_bytes(),'database copy differs')
            expect(application_data.FileStore(restored).download(fid)[0].read_bytes()=='仅测试备份恢复'.encode(),'attachment copy differs')
            application_data.FileStore(restored).delete(fid);expect(files.download(fid)[0].is_file(),'parent attachment was changed')
            expect(application_data.restore(restore_token,identifier).get('replayed'),'idempotent restore failed')
            return {'original':identifier,'restored':result['run_id'],'attachment_count':1,'parent_unchanged':True}
        case('business_data_backup_restore',data_roundtrip)
        def persistent_preview():
            session=preview.Preview(identifier)
            try:
                expect(session.frame['ok'] and session.frame['image'],'preview frame missing')
                (ROOT/'outputs/offline-fullstack-preview.png').write_bytes(base64.b64decode(session.frame['image']))
                expect(session.action({'action':'reload'})['ok'],'preview reload failed')
            finally:session.close()
            # A new browser must reopen the same application database.
            second=preview.Preview(identifier)
            try:expect(second.frame['ok'],'preview reopen failed')
            finally:second.close()
            return {'first_frame':True,'reload':True,'reopen':True}
        case('isolated_persistent_preview',persistent_preview)
        def delivery_roundtrip():
            state=store.get_run(identifier);state.update(status='completed',build_status='passed',test_status='passed',test_output='4 independent Playwright checks passed')
            state['artifact_paths']=runner.deliver(identifier,state);store.update_run(identifier,state)
            archive=collect_response(history.backup_project(uuid.UUID(identifier)));manifest,snapshots=recovery.inspect(archive)
            expect(snapshots,'backup has no recoverable sources');expect(manifest['completeness']=='complete',str(manifest.get('warnings')))
            original=project.sources(identifier);restored_id=str(uuid.uuid4());snapshot=next(v for v in snapshots if v['run_id']==identifier)
            runner.initialize_workspace(restored_id,application_type='fullstack_crud',crud_schema=SCHEMA);runner.apply_generated_files(restored_id,[{'path':k,'content':v} for k,v in snapshot['files'].items()],replace_all=True)
            expect(original==project.sources(restored_id),'source recovery mismatch')
            with zipfile.ZipFile(io.BytesIO(archive)) as z:expect(not any('.env' in name or 'app.sqlite3' in name for name in z.namelist()),'sensitive data entered source backup')
            return {'bytes':len(archive),'recoverable_snapshots':len(snapshots),'exact_sources':True,'source_export_excludes_database':True}
        case('git_source_export_backup_restore',delivery_roundtrip)
        def knowledge_roundtrip():
            app=FastAPI();app.include_router(library.router)
            with TestClient(app) as client:
                created=client.post('/api/v1/library/collections',json={'name':'M19 合成知识库','description':'独立验收数据'});expect(created.status_code==200,created.text);cid=created.json()['id']
                body='# 验收规则\n任务标题最多六十个字符。任务支持新增、删除以及刷新持久化。\n# 附件规则\n附件只保存在本地，不发给远程模型。'
                uploaded=client.post('/api/v1/library/collections/'+cid+'/documents?filename=验收规则.md',content=body.encode());expect(uploaded.status_code in {200,201,202},uploaded.text);doc=uploaded.json();did=doc.get('document_id') or doc.get('id')
                current=client.get('/api/v1/library/documents/'+did).json();expect(current['status']=='ready',str(current))
                found=client.post('/api/v1/library/retrieve',json={'query':'任务标题','collection_ids':[cid]}).json();expect(found['chunks'],'no retrieval evidence')
                duplicate=client.post('/api/v1/library/collections/'+cid+'/documents?filename=副本.md',content=body.encode());expect(duplicate.status_code<300,duplicate.text)
                removed=client.delete('/api/v1/library/documents/'+did);expect(removed.status_code<300,removed.text)
                after=client.post('/api/v1/library/retrieve',json={'query':'任务标题','collection_ids':[cid]});expect(after.status_code in {200,409},after.text)
                return {'upload_index_retrieve_delete':True,'provider':'local_full_text','duplicate_status':duplicate.status_code}
        case('local_knowledge_lifecycle',knowledge_roundtrip)
    after=saved_call_count();expect(before==after,'model usage changed unexpectedly')
    results['provider_ledger_unchanged']=True;results['completed_at']=time.time();REPORT.write_text(json.dumps(results,ensure_ascii=False,indent=2));print('ALL INTEGRATION CASES PASSED',flush=True)

if __name__=='__main__':main()
