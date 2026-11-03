"""Bounded cross-platform pipe readers and fixed-command process deadlines."""
import os,queue,subprocess,threading,time
from .security_policy import redact,OUTPUT_LIMIT
from .platform_support import kill_process_group, WindowsJob

class OutputLimitExceeded(subprocess.SubprocessError):pass

def environment(extra=None):
    allowed={'PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR','SYSTEMROOT','DOCKER_CONFIG','XDG_RUNTIME_DIR'}
    value={key:item for key,item in os.environ.items() if key in allowed}
    for key,item in (extra or {}).items():
        if key in allowed or key.startswith('GIT_CONFIG_') or key=='GIT_ATTR_NOSYSTEM':value[key]=item
    value['GIT_TERMINAL_PROMPT']='0';value['PYTHONUTF8']='1';return value

def run_bounded(args,*,timeout=30,cwd=None,env=None,check=False,max_output_bytes=OUTPUT_LIMIT):
    if not isinstance(args,list) or not all(isinstance(item,str) for item in args):raise ValueError('Only fixed argument lists are allowed')
    process=subprocess.Popen(args,cwd=cwd,env=environment(env),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    job=WindowsJob(process)
    events=queue.Queue(maxsize=16);stopped=threading.Event();chunks={'out':[],'err':[]};total=0;closed=0;deadline=time.monotonic()+timeout
    def put(value):
        while not stopped.is_set():
            try:events.put(value,timeout=.1);return
            except queue.Full:pass
    def read(stream,name):
        try:
            while not stopped.is_set():
                part=os.read(stream.fileno(),65536)
                if not part:break
                put((name,part))
        except (OSError,ValueError):pass
        finally:put((name,None))
    for stream,name in [(process.stdout,'out'),(process.stderr,'err')]:threading.Thread(target=read,args=(stream,name),daemon=True).start()
    try:
        while closed<2:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise subprocess.TimeoutExpired(args,timeout)
            try:name,part=events.get(timeout=remaining)
            except queue.Empty:raise subprocess.TimeoutExpired(args,timeout) from None
            if part is None:closed+=1;continue
            total+=len(part)
            if total>max_output_bytes:raise OutputLimitExceeded('命令输出超过保护上限，已停止进程。')
            chunks[name].append(part)
        process.wait(timeout=max(.01,deadline-time.monotonic()))
        result=subprocess.CompletedProcess(args,process.returncode,b''.join(chunks['out']).decode(errors='replace'),b''.join(chunks['err']).decode(errors='replace'))
        if check and result.returncode:raise subprocess.CalledProcessError(result.returncode,args,output=redact(result.stdout),stderr=redact(result.stderr))
        return result
    finally:
        stopped.set();job.close();kill_process_group(process)
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()

class BoundedLineReader:
    def __init__(self,stream,limit=8*1024*1024):
        self.queue=queue.Queue(maxsize=2);self.stopped=threading.Event();self.limit=limit
        def put(value):
            while not self.stopped.is_set():
                try:self.queue.put(value,timeout=.1);return
                except queue.Full:pass
        def read():
            try:
                while not self.stopped.is_set():
                    line=stream.readline(limit+1)
                    if not line:put(EOFError());return
                    if len(line)>limit:put(OutputLimitExceeded('预览响应超过8MiB上限。'));return
                    put(line)
            except (OSError,ValueError):put(EOFError())
        threading.Thread(target=read,daemon=True).start()
    def line(self,timeout):
        try:value=self.queue.get(timeout=timeout)
        except queue.Empty:raise TimeoutError('preview deadline') from None
        if isinstance(value,Exception):raise value
        return value
    def close(self):self.stopped.set()
