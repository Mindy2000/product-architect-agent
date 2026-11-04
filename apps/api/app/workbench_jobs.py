"""Persistent local maintenance jobs with cooperative cancellation and a request drain gate."""
import json,os,sqlite3,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager,closing
from . import workbench_archive as archive
from .security_policy import redact

class Gate:
 def __init__(self):self.condition=threading.Condition();self.owner=None;self.active=0
 def enter(self):
  with self.condition:
   if self.owner:return False
   self.active+=1;return True
 def leave(self):
  with self.condition:self.active-=1;self.condition.notify_all()
 def reserve(self,owner):
  with self.condition:
   if self.owner:return False
   self.owner=owner;return True
 def release(self,owner):
  with self.condition:
   if self.owner==owner:self.owner=None;self.condition.notify_all()
 @contextmanager
 def exclusive(self,owner,cancelled):
  if not self.reserve(owner):raise archive.ArchiveError('另一个维护操作正在进行，请稍后再试。','resource_busy')
  try:
   with self.condition:
    end=time.monotonic()+8
    while self.active:
     archive.checkpoint(None,cancelled)
     if time.monotonic()>end:raise archive.ArchiveError('仍有本地操作未结束，请先暂停开发或关闭预览。','resource_busy')
     self.condition.wait(.1)
   yield
  finally:self.release(owner)
gate=Gate();_pool=None;_pool_lock=threading.Lock()
TERMINAL={'completed','cancelled','failed','interrupted'}

def folder(root):
 path=archive.safe_path(root,'data/workbench/jobs');path.mkdir(parents=True,exist_ok=True,mode=0o700);return path

def connect(root):
 folder(root);path=archive.sqlite_path(root,'data/workbench/jobs/tasks.sqlite3')
 if path.is_symlink():raise archive.ArchiveError('维护任务记录路径异常。')
 conn=sqlite3.connect(path,timeout=10);conn.row_factory=sqlite3.Row
 conn.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT,status TEXT,options TEXT,result TEXT,error TEXT,phase TEXT,done INTEGER,total INTEGER,created REAL,started REAL,ended REAL,cancel_requested INTEGER NOT NULL DEFAULT 0)');conn.commit();return conn

def update(root,identifier,**values):
 allowed={'status','result','error','phase','done','total','started','ended','cancel_requested'}
 if not values.keys()<=allowed:raise ValueError('Unknown job fields')
 with closing(connect(root)) as c:c.execute('UPDATE tasks SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),identifier));c.commit()

def read(root,identifier):
 with closing(connect(root)) as c:row=c.execute('SELECT * FROM tasks WHERE id=?',(str(uuid.UUID(str(identifier))),)).fetchone()
 if row is None:return None
 value=dict(row);value['options']=json.loads(value['options']);value['result']=json.loads(value['result'] or '{}')
 value['elapsed_seconds']=round(max(0,(value['ended'] or time.time())-(value['started'] or value['created'])),1)
 value['progress_percent']=int(100*value['done']/max(1,value['total']));value['can_cancel']=value['status'] in {'queued','running'} and value['phase'] not in {'提交独立副本','备份完成'}
 artifact=archive.safe_path(root,'data/workbench/jobs/artifacts/'+value['id']+'.zip');value['artifact_available']=artifact.is_file() and not artifact.is_symlink()
 value['can_restart']=value['status'] in {'failed','interrupted','cancelled'};value['cancel_requested']=bool(value['cancel_requested']);return value

def recent(root):
 with closing(connect(root)) as c:ids=[row[0] for row in c.execute('SELECT id FROM tasks ORDER BY created DESC LIMIT 20')]
 return [read(root,i) for i in ids]

def cancelled(root,identifier):
 with closing(connect(root)) as c:row=c.execute('SELECT cancel_requested,status FROM tasks WHERE id=?',(identifier,)).fetchone()
 return not row or bool(row[0]) or row[1]=='interrupted'

def cancel(root,identifier):
 value=read(root,identifier)
 if not value:raise archive.ArchiveError('维护任务不存在。')
 if not value['can_cancel']:raise archive.ArchiveError('任务已结束或进入提交阶段，不能撤销已完成的操作。')
 update(root,str(identifier),cancel_requested=1)
 return read(root,identifier)

def recover(root):
 with closing(connect(root)) as c:
  c.execute("UPDATE tasks SET status='interrupted',error='服务已重启，维护任务未自动继续；可重新开始，原工作台保留。',ended=? WHERE status IN ('queued','running')",(time.time(),));c.commit()

def execute(root,identifier,operation):
 stopped=lambda:cancelled(root,identifier)
 def progress(phase,done,total):
  archive.checkpoint(None,stopped)
  update(root,identifier,phase=phase,done=max(0,int(done)),total=max(1,int(total)))
 try:
  archive.checkpoint(None,stopped)
  update(root,identifier,status='running',started=time.time(),phase='等待安全执行窗口')
  with gate.exclusive(identifier,stopped):
   result=operation(identifier,progress,stopped)
  update(root,identifier,status='completed',phase='已完成',done=1,total=1,result=json.dumps(result,ensure_ascii=False),ended=time.time())
 except archive.OperationCancelled:
  update(root,identifier,status='cancelled',phase='已取消',error='操作已取消，原工作台和原备份保留。',ended=time.time())
 except Exception as error:
  from .maintenance_errors import describe
  failure=describe(error)
  message=redact(str(error)) if isinstance(error,archive.ArchiveError) else failure['message']
  update(root,identifier,status='failed',phase='未完成',error=message,result=json.dumps({'failure':failure},ensure_ascii=False),ended=time.time())

def start(root,kind,options,operation):
 global _pool
 with _pool_lock:
  with closing(connect(root)) as c:
   c.execute('BEGIN IMMEDIATE')
   if c.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]>=4:raise archive.ArchiveError('已有四个维护任务，请等待或取消后再试。')
   identifier=str(uuid.uuid4());c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)',(identifier,kind,'queued',json.dumps(options),None,'','排队中',0,1,time.time(),None,None));c.commit()
  if _pool is None:_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='local-maintenance')
  _pool.submit(execute,root,identifier,operation)
 return read(root,identifier)

def shutdown(root):
 global _pool
 with _pool_lock:
  with closing(connect(root)) as c:c.execute("UPDATE tasks SET cancel_requested=1 WHERE status IN ('queued','running')");c.commit()
  if _pool:_pool.shutdown(wait=False,cancel_futures=True);_pool=None
