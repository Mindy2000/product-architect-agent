"""M19 regressions: synthetic data only; no provider requests or user-project mutations."""
import io,json,os,sqlite3,sys,tempfile,time,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI,Request,HTTPException,BackgroundTasks
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import security_policy as policy,process_control,main,runner,store,history,maintenance,source_import,project,recovery,research,public_http
from app.app_file_store import FileStore,FileStoreError
from app.crud_schema import validate_evolution,CrudSchema


def zip_bytes(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        for name,value in files.items():z.writestr(name,value)
    return output.getvalue()


class LocalInterfaceTests(unittest.TestCase):
    def setUp(self):
        app=FastAPI()
        @app.get('/ok')
        def ok():return {'ok':True}
        @app.post('/echo')
        async def echo(request:Request):return {'bytes':len(await request.body())}
        @app.get('/error')
        def error():raise HTTPException(409,'Bearer sk-syntheticcredential123456789')
        @app.get('/boom')
        def boom():raise RuntimeError('internal private context')
        app.add_middleware(policy.LocalSafetyMiddleware)
        self.app=app;self.client=TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',47001))
        self.addCleanup(self.client.close)
    def test_local_request_security_headers(self):
        response=self.client.get('/ok');self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store');self.assertEqual(response.headers['x-frame-options'],'DENY')
    def test_host_rebinding_is_rejected(self):
        self.assertEqual(self.client.get('/ok',headers={'Host':'attacker.example'}).status_code,400)
    def test_cross_origin_reads_and_mutations_are_rejected(self):
        for method,path in [('GET','/ok'),('POST','/echo')]:
            self.assertEqual(self.client.request(method,path,headers={'Origin':'https://attacker.example'}).status_code,403)
    def test_cross_site_fetch_without_origin_is_rejected(self):
        self.assertEqual(self.client.get('/ok',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
    def test_remote_peer_is_rejected(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8000',client=('192.168.1.9',47002)) as remote:
            self.assertEqual(remote.get('/ok').status_code,403)
    def test_local_origin_is_allowed(self):
        self.assertEqual(self.client.post('/echo',content=b'abc',headers={'Origin':'http://127.0.0.1:5173'}).json()['bytes'],3)
    def test_declared_and_streamed_body_limits(self):
        self.assertEqual(self.client.post('/echo',content=b'x'*(1024*1024+1)).status_code,413)
        self.assertEqual(self.client.post('/echo',content=iter([b'x'*700000,b'y'*700000])).status_code,413)
    def test_error_response_is_redacted_and_traceable(self):
        response=self.client.get('/error');self.assertEqual(response.status_code,409)
        self.assertNotIn('sk-synthetic',response.text);self.assertIn('request_id',response.json())
    def test_unhandled_error_hides_internal_detail(self):
        response=self.client.get('/boom');self.assertEqual(response.status_code,500);self.assertNotIn('private context',response.text)
    def test_request_rate_limit(self):
        for _ in range(120):self.assertEqual(self.client.post('/echo',content=b'a').status_code,200)
        self.assertEqual(self.client.post('/echo',content=b'a').status_code,429)
    def test_validation_does_not_echo_input(self):
        client=TestClient(main.app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',47003))
        self.addCleanup(client.close)
        response=client.post('/api/v1/runs',json={'design_id':'synthetic-private-input','design_revision':1})
        self.assertEqual(response.status_code,422);self.assertNotIn('synthetic-private-input',response.text)
    def test_secret_exact_match_is_removed(self):
        with patch.dict(os.environ,{'ACCEPTANCE_API_KEY':'synthetic-secret-value-123456'}),patch.object(policy,'_secret_stamp',None):
            self.assertNotIn('synthetic-secret-value',policy.redact('failure: synthetic-secret-value-123456'))


class ProcessBoundaryTests(unittest.TestCase):
    def test_output_flood_is_stopped(self):
        with self.assertRaises(process_control.OutputLimitExceeded):process_control.run_bounded([sys.executable,'-c','import sys;sys.stdout.write("x"*3000000)'],timeout=5)
    def test_deadline_is_enforced(self):
        import subprocess
        start=time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):process_control.run_bounded([sys.executable,'-c','import time;time.sleep(10)'],timeout=.15)
        self.assertLess(time.monotonic()-start,3)
    def test_keys_do_not_reach_child_but_stdout_stays_exact(self):
        with patch.dict(os.environ,{'MODEL_API_KEY':'synthetic-key-never-pass'}):
            result=process_control.run_bounded([sys.executable,'-c','import os;print("MODEL_API_KEY" in os.environ);print("source-byte-marker")'])
        self.assertEqual(result.stdout,'False\nsource-byte-marker\n')
    def test_shell_command_string_is_rejected(self):
        with self.assertRaises(ValueError):process_control.run_bounded('echo unsafe')
    def test_artifact_path_link_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(policy,'ROOT',Path(tmp)):
            out=Path(tmp)/'outputs';out.mkdir();(out/'safe.txt').write_text('synthetic')
            self.assertEqual(policy.safe_output('outputs/safe.txt').read_text(),'synthetic')
            (out/'linked.txt').symlink_to(out/'safe.txt')
            for value in ['outputs/../safe.txt','/etc/passwd','outputs/linked.txt']:
                with self.subTest(path=value),self.assertRaises(ValueError):policy.safe_output(value)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.files=FileStore(Path(self.tmp.name))
    def upload(self,name='实验.txt',content='仅用于验收'.encode()):
        identifier=self.files.begin(name);self.files.path(identifier,'.part').write_bytes(content);return self.files.finish(identifier)
    def test_upload_download_hash_and_explicit_delete(self):
        row=self.upload();path,saved=self.files.download(row['id']);self.assertEqual(path.read_text(),'仅用于验收')
        self.assertEqual(self.files.list()['total'],1);self.files.delete(row['id']);self.assertEqual(self.files.list()['total'],0)
    def test_filename_and_signature_rejected(self):
        for name in ['../secret.txt','a.exe','a\x00.txt']:
            with self.assertRaises(FileStoreError):self.files.begin(name)
        identifier=self.files.begin('fake.png');self.files.path(identifier,'.part').write_bytes(b'not-image')
        with self.assertRaises(FileStoreError):self.files.finish(identifier)
        self.files.abort(identifier)
    def test_download_detects_tampering(self):
        row=self.upload();self.files.path(row['id']).write_bytes(b'changed')
        with self.assertRaises(FileStoreError):self.files.download(row['id'])
    def test_cleanup_preserves_ready_files(self):
        row=self.upload();identifier=self.files.begin('unfinished.txt');self.files.path(identifier,'.part').write_bytes(b'temporary')
        with self.files.connect() as conn:conn.execute('UPDATE files SET created_at=? WHERE id=?',(time.time()-90000,identifier))
        self.files.cleanup();self.assertEqual(self.files.download(row['id'])[1]['status'],'ready')
        with self.assertRaises(FileStoreError):self.files.get(identifier)
    def test_upload_root_symlink_rejected(self):
        (Path(self.tmp.name)/'uploads').symlink_to(Path(self.tmp.name))
        with self.assertRaises(FileStoreError):FileStore(Path(self.tmp.name))


class ImportAndPermissionTests(unittest.TestCase):
    def archive(self,extra=None):
        return zip_bytes({'package.json':json.dumps({'dependencies':{'react':'19'}}),'src/main.tsx':"import './style.css'",'src/App.tsx':'export default function App(){return <h1>验收</h1>}','src/style.css':'body{color:black}',**(extra or {})})
    def test_import_preview_token_accepts_typed_uuid(self):
        token=uuid.uuid4()
        with tempfile.TemporaryDirectory() as tmp,patch.object(source_import,'ROOT',Path(tmp)):
            self.assertEqual(source_import.preview_path(token).stem,str(token))

    def test_import_keeps_source_and_discards_untrusted_commands_and_env(self):
        result=source_import.inspect(self.archive({'scripts/install.sh':'exit 1','.env':'SYNTHETIC=true'}))
        self.assertEqual(result['blocked'],[]);self.assertNotIn('.env',result['files']);self.assertNotIn('scripts/install.sh',result['files'])
    def test_import_rejects_traversal_and_unsupported_dependency(self):
        with self.assertRaises(ValueError):source_import.inspect(self.archive({'../escape.txt':'unsafe'}))
        result=source_import.inspect(self.archive({'package.json':json.dumps({'dependencies':{'react':'19','unknown-test-only-package':'1'}})}))
        self.assertTrue(result['blocked'])
    def test_public_url_credentials_and_local_network_are_blocked(self):
        for value in ['http://example.com','https://127.0.0.1/','https://localhost/','https://user:pass@example.com/','https://example.com/?api_key=secret','https://example.com/login']:
            with self.subTest(url=value),self.assertRaises(ValueError):public_http.normalize(value)
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','fe80::1']:self.assertFalse(public_http.public(address))
    def test_public_html_drops_active_and_form_content(self):
        title,text=research.plain('<title>Guide</title><script>secret()</script><form>submit this</form><p>可信参考</p>')
        self.assertEqual(title,'Guide');self.assertIn('可信参考',text);self.assertNotIn('secret()',text);self.assertNotIn('submit this',text)
    def test_network_tools_require_explicit_task_permission(self):
        with patch.object(store,'get_run',return_value={'research_enabled':False}):
            with self.assertRaises(research.ResearchError):research.require_run(str(uuid.uuid4()),True)
    def test_schema_only_allows_additive_changes(self):
        original={'version':1,'entities':[{'name':'tasks','label':'任务','fields':[{'name':'title','type':'text','required':True}]}]}
        additive=json.loads(json.dumps(original));additive['version']=2;additive['entities'][0]['fields'].append({'name':'notes','type':'text'})
        validate_evolution(original,additive)
        for change in ['type','required','remove']:
            bad=json.loads(json.dumps(additive))
            if change=='remove':bad['entities'][0]['fields']=bad['entities'][0]['fields'][1:]
            elif change=='type':bad['entities'][0]['fields'][0]['type']='number'
            else:bad['entities'][0]['fields'][0]['required']=False
            with self.subTest(change=change),self.assertRaises(ValueError):validate_evolution(original,bad)
        with self.assertRaises(ValueError):CrudSchema.model_validate({'version':1,'entities':[{'name':'sqlite_master','label':'bad','fields':[{'name':'x','type':'text'}]}]})


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        for target,name,value in [(store,'DATA_DIR',self.root),(store,'RUNS_DB',self.root/'runs.sqlite3')]:
            item=patch.object(target,name,value);item.start();self.addCleanup(item.stop)
        self.ids=[str(uuid.uuid4()) for _ in range(4)]
        for index,identifier in enumerate(self.ids):
            state={'run_id':identifier,'status':'paused','acceptance_criteria':['独立验收'],'executor_tasks':[{'id':'task-1'}]}
            if index:state['parent_run_id']=self.ids[0 if index<3 else 1]
            store.create_run(identifier,'Synthetic acceptance',state,'2026-11-03')
    def test_project_tree_includes_siblings_and_grandchildren(self):
        tree,warnings=history.project_tree(self.ids[1]);self.assertEqual({s['run_id'] for s,d in tree},set(self.ids));self.assertEqual(warnings,[])
    def test_project_tree_cycle_is_rejected(self):
        state=store.get_run(self.ids[0]);state['parent_run_id']=self.ids[3];store.update_run(self.ids[0],state)
        with self.assertRaises(HTTPException):history.project_tree(self.ids[1])
    def test_maintenance_optimistic_revision_and_unknown_links(self):
        identifier=uuid.UUID(self.ids[0]);payload=maintenance.Record(revision=0,summary='验收变更记录')
        with patch.object(runner,'read_generated_sources',return_value={'src/App.tsx':'synthetic'}):
            self.assertEqual(maintenance.save_record(identifier,payload)['revision'],1)
            with self.assertRaises(HTTPException):maintenance.save_record(identifier,payload)
            with self.assertRaises(HTTPException):maintenance.save_record(identifier,maintenance.Record(revision=1,links=[{'criterion':9,'task_id':'task-1','files':[]}]))
    def test_resume_queue_capacity_does_not_schedule_work(self):
        from types import SimpleNamespace
        with patch.object(main,'active_runs',set(str(i) for i in range(policy.MAX_QUEUED_RUNS))),patch.object(main.store,'get_run',return_value={'repair_round':0}),patch.object(main.store,'cancellation',return_value=False),patch.object(main,'workflow',SimpleNamespace(get_state=lambda cfg:SimpleNamespace(next=['build']))):
            tasks=BackgroundTasks()
            with self.assertRaises(HTTPException) as error:main.resume_run(str(uuid.uuid4()),tasks)
            self.assertEqual(error.exception.status_code,429);self.assertEqual(tasks.tasks,[])

if __name__=='__main__':unittest.main()
