"""Local install/configuration/launch CLI. Standard library only until dependencies are installed."""
from __future__ import annotations
import argparse,getpass,http.server,json,os,platform,re,shutil,signal,socket,subprocess,sys,threading,time,urllib.request,webbrowser
from pathlib import Path
from distribution import DistributionError,ROOT,build,inspect,upgrade,digest,ensure_idle


def environment():
    value={k:v for k,v in os.environ.items() if k in {'PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR','DOCKER_CONFIG','XDG_RUNTIME_DIR','SYSTEMROOT'}}
    value['PYTHONUTF8']='1';return value

def run(args,root=ROOT,timeout=None):
    result=subprocess.run([str(x) for x in args],cwd=root,env=environment(),timeout=timeout)
    if result.returncode:raise DistributionError('步骤未完成，请根据上方提示处理后重试。')

def capture(args):
    try:
        result=subprocess.run(args,env=environment(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,text=True)
        return result.stdout.strip()[:8192] if result.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None

def safe_root(root):
    if root.is_symlink():raise DistributionError('项目目录不能是符号链接。')
    for name in ['.env','.venv','node_modules','data','apps/web/dist']:
        if (root/name).is_symlink():raise DistributionError('受控路径不能是符号链接：'+name)

def venv_python(root):return root/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')

def docker_context(root):return read_config(root).get('PA_DOCKER_CONTEXT') or ('default' if os.name=='nt' else 'colima')

def doctor(root=ROOT):
    safe_root(root);built=(root/'apps/web/dist/index.html').is_file();free=shutil.disk_usage(root).free
    checks=[{'name':'macOS / Windows','ok':platform.system() in {'Darwin','Windows'},'required':True,'detail':'macOS使用Colima，Windows使用Docker Desktop Linux容器'},
            {'name':'Python 3.13','ok':sys.version_info[:2]==(3,13),'required':True,'detail':platform.python_version()},
            {'name':'Node.js 24（源码构建）','ok':bool(shutil.which('node') and (capture(['node','--version']) or '').startswith('v24.')),'required':not built,'detail':'预构建安装包无需宿主Node；源码安装需要Node 24'},
            {'name':'Git','ok':bool(shutil.which('git') and capture(['git','--version'])),'required':True,'detail':'用于本地受控版本快照'},
            {'name':'Docker CLI','ok':bool(shutil.which('docker')),'required':True,'detail':'生成代码仅在容器中执行'},
            {'name':'Colima','ok':bool(shutil.which('colima')),'required':os.name!='nt','detail':'macOS使用Colima；Windows不需要Colima'},
            {'name':'可用磁盘','ok':free>=2*1024**3,'required':True,'detail':f'{free/1024**3:.1f} GiB；首次镜像准备建议另外预留8 GiB'},
            {'name':'本地.env','ok':(root/'.env').is_file(),'required':False,'detail':'Key仅保存在本地，不在诊断中显示'},
            {'name':'Tesseract（可选OCR）','ok':bool(shutil.which('tesseract')),'required':False,'detail':'不影响直接文本和非OCR路径'}]
    return {'ok':all(c['ok'] for c in checks if c['required']),'checks':checks,'prebuilt_frontend':built,'config_values_hidden':True}

def init_config(root=ROOT):
    safe_root(root);path=root/'.env'
    if path.exists():path.chmod(0o600);return False
    raw=(root/'.env.example').read_bytes();fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw)
    return True

def read_config(root=ROOT):
    path=root/'.env'
    if path.is_symlink():raise DistributionError('配置不允许链接。')
    result={}
    if path.exists():
        if path.stat().st_size>65536:raise DistributionError('配置文件超限。')
        for line in path.read_text().splitlines():
            m=re.match(r'^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$',line)
            if m:result[m[1]]=m[2].strip().strip('"\'')
    return result

def check_config(root=ROOT):
    values=read_config(root);checks=[]
    for title,key,flag in [('DeepSeek文字','MODEL_API_KEY','MODEL_CALLS_ENABLED'),('千问视觉','VISION_API_KEY','VISION_CALLS_ENABLED'),('Dify知识库','RAG_API_KEY',None)]:
        checks.append({'provider':title,'key_configured':bool(values.get(key)),'enabled':values.get(flag,'false').lower()=='true' if flag else '按知识库设置显式启用'})
    try:budget=float(values.get('MODEL_BUDGET_CNY','99'));valid=0<budget<=99
    except ValueError:valid=False
    return {'providers':checks,'budget_valid':valid,'key_values_hidden':True,'network_requests':0}

def save_key(root,provider,key):
    if provider not in {'deepseek','qwen','dify'} or not key or len(key)>1024 or re.search(r'[\s\x00-\x1f\x7f"\'\\#]',key):raise DistributionError('Key格式无效，未写入。')
    init_config(root);name={'deepseek':'MODEL_API_KEY','qwen':'VISION_API_KEY','dify':'RAG_API_KEY'}[provider];path=root/'.env';lines=path.read_text().splitlines();found=False;out=[]
    for line in lines:
        if re.match(r'^'+name+r'\s*=',line):
            if not found:out.append(name+'='+key);found=True
        else:out.append(line)
    if not found:out.append(name+'='+key)
    temp=path.with_name('.env.write-'+str(os.getpid()));fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:stream.write('\n'.join(out)+'\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def install(root=ROOT,system=False,ocr=False,prepare_runtime=False,plan=False):
    safe_root(root)
    if sys.version_info[:2]!=(3,13):raise DistributionError('请使用Python 3.13运行安装入口。')
    built=(root/'apps/web/dist/index.html').is_file();commands=[]
    if system:
        if os.name=='nt':raise DistributionError('Windows请先安装Python 3.13、Git和Docker Desktop；不会自动启用WSL或重启电脑。')
        brew=shutil.which('brew')
        if not brew:raise DistributionError('未检测到Homebrew，请先按docs/SETUP.md准备基础工具。')
        packages=[name for name in ['git','docker','colima'] if not shutil.which(name)]
        if not built and not shutil.which('node'):packages.append('node@24')
        if ocr and not shutil.which('tesseract'):packages.append('tesseract')
        if packages:commands.append([brew,'install',*packages])
    commands += [[sys.executable,'-m','venv',str(root/'.venv')],[str(venv_python(root)),'-m','pip','install','--no-index','--no-deps',str(root/'runtime/bootstrap/pip-26.2.2-py3-none-any.whl')],[str(venv_python(root)),'-m','pip','install','--no-cache-dir','-r',str(root/'requirements.lock')]]
    if not built:commands += [['npm','ci','--ignore-scripts','--no-audit','--no-fund'],['npm','--workspace','apps/web','run','build']]
    if ocr:commands.append([str(venv_python(root)),str(root/'scripts/install_local_ocr.py')])
    if prepare_runtime:
        if os.name!='nt':commands.append(['colima','start','--runtime','docker','--vm-type','vz','--cpu','2','--memory','2','--disk','12'])
        commands += [['docker','--context',docker_context(root),'build','--tag','product-architect-worker:1.0','--file','runtime/worker/Dockerfile','runtime/worker'],['docker','--context',docker_context(root),'build','--tag','product-architect-fullstack:1.0','--file','runtime/fullstack/Dockerfile','runtime/fullstack']]
    if plan:return {'commands':commands,'prebuilt_frontend':built,'model_calls':0,'applied':False}
    # Installing into a running checkout could replace imported dependencies.
    ensure_idle(root)
    if platform.system() not in {'Darwin','Windows'}:raise DistributionError('当前安装入口支持macOS和Windows。')
    if shutil.disk_usage(root).free<2*1024**3:raise DistributionError('安装依赖前至少保留2GiB空间；容器镜像另计。')
    bootstrap=json.loads((root/'runtime/bootstrap/manifest.json').read_text())
    wheel=root/'runtime/bootstrap/pip-26.2.2-py3-none-any.whl'
    if bootstrap.get('filename')!=wheel.name or digest(wheel.read_bytes())!=bootstrap.get('sha256'):raise DistributionError('pip引导包校验失败，未执行安装。')
    init_config(root)
    for command in commands:run(command,root)
    state=root/'data';state.mkdir(exist_ok=True,mode=0o700)
    (state/'install-receipt.json').write_text(json.dumps({'python':platform.python_version(),'requirements_sha256':digest((root/'requirements.lock').read_bytes()),'frontend_ready':True,'model_calls':0},ensure_ascii=False,indent=2));(state/'install-receipt.json').chmod(0o600)
    return {'applied':True,'next':'安装完成。请运行配置入口填写自己的Key，再运行启动入口；调用默认关闭。','model_calls':0}

class Frontend(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def end_headers(self):
        for key,value in {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"}.items():self.send_header(key,value)
        super().end_headers()
    def list_directory(self,path):self.send_error(404);return None
    def do_GET(self):
        if self.headers.get('Host') not in {'127.0.0.1:5173','localhost:5173'}:self.send_error(400);return
        from urllib.parse import unquote,urlsplit
        name=unquote(urlsplit(self.path).path)
        if '\\' in name or any(part.startswith('.') or part=='..' for part in name.split('/')):self.send_error(404);return
        if name=='/':self.path='/index.html';name='/index.html'
        base=Path(self.directory).resolve();parts=name.lstrip('/').split('/');target=base.joinpath(*parts)
        if base not in target.resolve().parents or any(base.joinpath(*parts[:i]).is_symlink() for i in range(1,len(parts)+1)):self.send_error(404);return
        return super().do_GET()
    def do_HEAD(self):
        # Apply the same path/Host gate while suppressing the body.
        return self.do_GET()
    def copyfile(self,source,outputfile):
        if self.command!='HEAD':super().copyfile(source,outputfile)

def launch(root=ROOT,no_open=False):
    safe_root(root)
    if not venv_python(root).is_file() or not (root/'apps/web/dist/index.html').is_file():raise DistributionError('请先运行安装入口。')
    init_config(root)
    for port in [5173,8000]:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port))==0:raise DistributionError(f'端口{port}已占用，请先停止现有服务。')
    if not shutil.which('docker'):raise DistributionError('请先安装Docker CLI；Windows请安装并启动Docker Desktop的Linux容器后端。')
    if not capture(['docker','--context',docker_context(root),'info','--format','{{.ServerVersion}}']):
        if os.name=='nt':raise DistributionError('请先启动Docker Desktop，并确认已启用Linux容器后端。')
        run(['colima','start','--runtime','docker','--vm-type','vz','--cpu','2','--memory','2','--disk','12'],root)
    os.chdir(root);state=root/'data';state.mkdir(exist_ok=True,mode=0o700)
    log=state/'api.log'
    if log.is_symlink():raise DistributionError('日志路径异常。')
    if log.exists() and log.stat().st_size>5*1024*1024:
        previous=state/'api.previous.log'
        if previous.is_symlink():raise DistributionError('旧日志路径异常。')
        os.replace(log,previous)
    stream=open(log,'ab');log.chmod(0o600)
    args=[str(venv_python(root)),'-m','uvicorn','app.main:app','--app-dir',str(root/'apps/api'),'--host','127.0.0.1','--port','8000','--no-proxy-headers','--no-access-log','--limit-concurrency','64','--backlog','64','--timeout-keep-alive','5','--timeout-graceful-shutdown','15']
    child=subprocess.Popen(args,cwd=root,env=environment(),stdout=stream,stderr=stream,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    server=None
    def stop_signal(*args):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    try:
        (state/'api.pid').write_text(str(child.pid)+'\n');(state/'web.pid').write_text(str(os.getpid())+'\n')
        handler=lambda *a,**kw:Frontend(*a,directory=str(root/'apps/web/dist'),**kw)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',5173),handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));ready=False
        for _ in range(40):
            if child.poll() is not None:break
            try:
                with opener.open('http://127.0.0.1:8000/health',timeout=1) as response:ready=response.status==200
            except OSError:pass
            if ready:break
            time.sleep(.5)
        if not ready:raise DistributionError('API启动未完成，请查看项目data/api.log（不要公开其中资料）。')
        print('工作台已启动：http://127.0.0.1:5173/；保持窗口打开，退出时停止服务。')
        if not no_open:webbrowser.open('http://127.0.0.1:5173/')
        child.wait()
    except KeyboardInterrupt:pass
    finally:
        if server:server.shutdown();server.server_close()
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        stream.close()
        for name,pid in [('api',child.pid),('web',os.getpid())]:
            path=state/(name+'.pid')
            if path.is_file() and not path.is_symlink() and path.read_text().strip()==str(pid):path.unlink()

def stop(root=ROOT):
    for name in (['api','web'] if os.name=='nt' else ['web','api']):
        path=root/'data'/(name+'.pid')
        if path.is_symlink():raise DistributionError('进程记录异常。')
        if not path.is_file():continue
        value=path.read_text().strip()
        if not value.isdigit() or int(value)<=1:raise DistributionError('无效进程记录，未停止其他程序。')
        pid=int(value)
        if os.name=='nt':
            import psutil
            try:process=psutil.Process(pid)
            except psutil.NoSuchProcess:continue
            cmd=' '.join(process.cmdline())
            if Path(process.cwd()).resolve()!=root.resolve() or not any(marker in cmd for marker in ['uvicorn app.main:app','scripts/manage.py serve','scripts\\manage.py serve']):raise DistributionError('进程不属于当前项目，未停止。')
            if name=='api':
                process.send_signal(signal.CTRL_BREAK_EVENT)
                try:process.wait(timeout=20)
                except psutil.TimeoutExpired:process.terminate();process.wait(timeout=10)
            else:process.terminate();process.wait(timeout=10)
            continue
        cwd=capture(['lsof','-a','-p',str(pid),'-d','cwd','-Fn']);cmd=capture(['ps','-p',str(pid),'-o','command='])
        if not cmd:continue
        if not cwd or ('n'+str(root)) not in cwd.splitlines() or not any(marker in cmd for marker in ['uvicorn app.main:app','scripts/manage.py serve','node_modules/.bin/vite']):raise DistributionError('记录的进程不属于此项目，未停止。')
        os.kill(pid,signal.SIGTERM)
        deadline=time.monotonic()+30
        while True:
            state=capture(['ps','-p',str(pid),'-o','stat='])
            if not state or state.startswith('Z'):break
            if time.monotonic()>=deadline:raise DistributionError('服务仍在停止，请等待当前处理完成后再启动；未强制终止或删除数据。')
            time.sleep(.1)
    return {'stopped':True,'data_preserved':True}

def main():
    parser=argparse.ArgumentParser(description='Product Architect 本地安装与维护')
    commands=parser.add_subparsers(dest='action',required=True)
    commands.add_parser('doctor');config=commands.add_parser('config');config.add_argument('mode',choices=['init','check','key','wizard']);config.add_argument('--provider',choices=['deepseek','qwen','dify'],default='deepseek')
    setup=commands.add_parser('install');setup.add_argument('--system',action='store_true');setup.add_argument('--ocr',action='store_true');setup.add_argument('--prepare-runtime',action='store_true');setup.add_argument('--plan',action='store_true')
    server=commands.add_parser('serve');server.add_argument('--no-open',action='store_true');commands.add_parser('stop')
    pack=commands.add_parser('package');pack.add_argument('--kind',choices=['source','macos-install','windows-install'],default='macos-install');pack.add_argument('--output',default='release-artifacts');pack.add_argument('--version')
    verify=commands.add_parser('verify');verify.add_argument('archive')
    update=commands.add_parser('upgrade');update.add_argument('archive');update.add_argument('--destination',required=True);update.add_argument('--apply',action='store_true')
    rollback=commands.add_parser('rollback-info')
    args=parser.parse_args()
    if args.action=='doctor':value=doctor()
    elif args.action=='config':
        if args.mode=='init':value={'created':init_config(),'key_values_hidden':True}
        elif args.mode=='check':value=check_config()
        else:
            provider=args.provider
            if args.mode=='wizard':
                print('1 DeepSeek文字；2 千问视觉；3 Dify知识库。Key只保存在本地，不会显示或上传。')
                provider={'1':'deepseek','2':'qwen','3':'dify'}.get(input('选择供应商 [1]：').strip() or '1')
                if not provider:raise DistributionError('选项无效。')
            save_key(ROOT,provider,getpass.getpass('粘贴API Key（不会显示输入）：'))
            value={'saved':True,'calls_automatically_enabled':False,'next':'是否允许调用由.env中的独立开关控制；修改后重启。'}
    elif args.action=='install':value=install(system=args.system,ocr=args.ocr,prepare_runtime=args.prepare_runtime,plan=args.plan)
    elif args.action=='serve':launch(no_open=args.no_open);return
    elif args.action=='stop':value=stop()
    elif args.action=='package':value={'archive':str(build(ROOT,Path(args.output),args.kind,args.version))}
    elif args.action=='verify':m=inspect(args.archive);value={k:m[k] for k in ['product','version','kind']}
    elif args.action=='upgrade':value=upgrade(ROOT,args.archive,Path(args.destination),args.apply)
    else:
        path=ROOT/'data/upgrade-receipt.json'
        if path.is_symlink() or not path.is_file():raise DistributionError('当前目录没有并排升级记录。')
        value=json.loads(path.read_text());value['instructions']='先停止当前版本。旧目录仍完整保留，可从旧目录启动；若新版本已有新资料，请先导出备份，不自动用旧数据覆盖新数据。'
    print(json.dumps(value,ensure_ascii=False,indent=2))

if __name__=='__main__':
    if os.name=='nt' and not sys.flags.utf8_mode:os.execv(sys.executable,[sys.executable,'-X','utf8',*sys.argv])
    try:main()
    except (DistributionError,OSError) as error:
        print(str(error) if isinstance(error,DistributionError) else '本地步骤未完成，请检查磁盘、权限和基础工具；原目录保留。',file=sys.stderr);raise SystemExit(1)
