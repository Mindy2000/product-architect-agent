"""Interactive screenshots and input through a network-disabled browser container."""
import json
import os
from .process_control import environment, BoundedLineReader, OutputLimitExceeded
import select
import subprocess
import threading
import time
import uuid
from .runner import ROOT, WORKER_IMAGE, DOCKER_CONTEXT, SECCOMP_PROFILE, workspace_for, SandboxError

class _Preview:
    def __init__(self, run_id):
        workspace=workspace_for(run_id)
        if not (workspace/'dist/index.html').is_file():
            raise SandboxError('该项目还没有可预览的构建结果。')
        from . import project,runtime_identity
        self.source_digest=project.manifest(run_id)['digest']
        self.environment=runtime_identity.require(run_id)
        self.lock=threading.Lock()
        self.used=time.monotonic()
        self.name='pa-preview-'+uuid.uuid4().hex[:16]
        from .container_owner import LABELS
        command=['docker','--context',DOCKER_CONTEXT,'run','--rm','-i','--init','--name',self.name,*LABELS,
          '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true',
          f'--security-opt=seccomp={SECCOMP_PROFILE}','--pids-limit=128','--memory=1536m','--cpus=1.5','--shm-size=384m',
          '--mount',f'type=bind,src={workspace},dst=/workspace',
          '--mount',f'type=bind,src={ROOT / "runtime/preview-worker.cjs"},dst=/runner/preview.cjs,readonly',
          '--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--workdir=/workspace','--user=pwuser',
          '--env','HOME=/tmp/worker-home','--env','PLAYWRIGHT_BROWSERS_PATH=/ms-playwright',
          '--entrypoint=node',WORKER_IMAGE,'/runner/preview.cjs']
        from . import fullstack,project
        if fullstack.application_type(run_id)=='fullstack_crud':
            project.sources(run_id)
            directory=fullstack.data_dir(run_id)
            if not (directory/'app.sqlite3').is_file():raise SandboxError('请先在业务后端面板显式初始化数据库。')
            command[command.index(WORKER_IMAGE)]=fullstack.IMAGE
            index=command.index('--tmpfs')
            command[index:index]=['--mount',f'type=bind,src={directory},dst=/appdata','--env','PA_FULLSTACK=1','--env','PYTHONPYCACHEPREFIX=/tmp/pa-pycache','--env','PYTHONDONTWRITEBYTECODE=1']
        image_index=command.index(fullstack.IMAGE if fullstack.application_type(run_id)=='fullstack_crud' else WORKER_IMAGE)
        command[image_index]=self.environment['image_id']
        self._buffer=b''
        self.process=subprocess.Popen(command,env=environment(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1)
        try:self.frame=self.read(90)
        except Exception:self.close();raise
    def read(self, timeout=30):
        if not hasattr(self,'reader'):self.reader=BoundedLineReader(self.process.stdout)
        try:line=self.reader.line(timeout)
        except (TimeoutError,EOFError,OutputLimitExceeded):raise SandboxError('预览响应超时、结束或超过8MiB上限，请重新打开。') from None
        try:data=json.loads(line)
        except ValueError:raise SandboxError('预览进程已结束，请重新打开。')
        if not data.get('ok'):raise SandboxError(data.get('error','预览失败'))
        return data
    def action(self, payload):
        with self.lock:
            self.used=time.monotonic()
            try:
                self.process.stdin.write(json.dumps(payload)+'\n');self.process.stdin.flush()
                self.frame=self.read()
                return self.frame
            except (BrokenPipeError,OSError):raise SandboxError('预览已断开，请重新打开。')
    def close(self):
        if hasattr(self,'reader'):self.reader.close()
        subprocess.run(['docker','--context',DOCKER_CONTEXT,'rm','-f',self.name],env=environment(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        self.process.terminate()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if self.process.stdin:self.process.stdin.close()
        if self.process.stdout:self.process.stdout.close()

class Preview(_Preview):
    def __init__(self,run_id):
        from .runtime_limits import worker_slot
        workspace=workspace_for(run_id)
        if workspace.parent.name!='generated-projects':raise SandboxError('预览工作区路径异常。')
        self._lease=worker_slot(workspace.parent.parent)
        self._lease.__enter__()
        try:super().__init__(run_id)
        except BaseException:
            self._release_slot();raise
    def _release_slot(self):
        if self._lease is not None:
            lease=self._lease;self._lease=None;lease.__exit__(None,None,None)
    def close(self):
        try:super().close()
        finally:self._release_slot()


sessions={}
lock=threading.Lock()
reaper_stop=threading.Event()
reaper_started=False

def reap_loop():
    while not reaper_stop.wait(60):
        with lock:
            expired=[rid for rid,session in sessions.items() if time.monotonic()-session.used>900]
            for rid in expired:
                try:sessions.pop(rid).close()
                except Exception:pass

def open_preview(run_id):
    from .project import project_lock
    with project_lock(run_id):return _open_preview(run_id)


def _open_preview(run_id):
    global reaper_started
    with lock:
        if not reaper_started:
            threading.Thread(target=reap_loop,daemon=True).start()
            reaper_started=True
        for rid in list(sessions):
            if time.monotonic()-sessions[rid].used>900:
                sessions.pop(rid).close()
        if run_id in sessions:
            if sessions[run_id].process.poll() is None:
                return sessions[run_id].action({'action':'snapshot'})
            sessions.pop(run_id).close()
        if len(sessions)>=2:
            raise SandboxError('最多同时打开两个预览，请先关闭一个。')
        sessions[run_id]=Preview(run_id)
        return sessions[run_id].frame

def act(run_id,payload):
    with lock: session=sessions.get(run_id)
    if not session:raise SandboxError('预览未打开或已经关闭。')
    return session.action(payload)

def close_preview(run_id):
    with lock:session=sessions.pop(run_id,None)
    if session:session.close()

def close_all():
    reaper_stop.set()
    for rid in list(sessions):close_preview(rid)
