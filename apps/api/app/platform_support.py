"""Host differences; generated code still runs only in Linux containers."""
import os,time
from contextlib import contextmanager

def kill_process_group(process):
    if os.name=='nt':
        import psutil
        try:
            parent=psutil.Process(process.pid);children=parent.children(recursive=True)
            for member in reversed(children):
                try:member.kill()
                except psutil.NoSuchProcess:pass
            try:parent.kill()
            except psutil.NoSuchProcess:pass
        except psutil.NoSuchProcess:pass
    else:
        import signal
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    process.wait()

@contextmanager
def exclusive_file(stream,timeout=60):
    if os.name=='nt':
        import msvcrt
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b'\0');stream.flush()
        def acquire():stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        def release():stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        def acquire():fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def release():fcntl.flock(stream,fcntl.LOCK_UN)
    deadline=time.monotonic()+timeout
    while True:
        try:acquire();break
        except OSError:
            if time.monotonic()>=deadline:raise TimeoutError('local document worker busy')
            time.sleep(.1)
    try:yield
    finally:release()

class WindowsJob:
    """Kill-on-close Job Object: bounds children even if a Windows parent exits early."""
    def __init__(self,process):
        self.handle=None
        if os.name!='nt':return
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('ProcessTime',ctypes.c_longlong),('JobTime',ctypes.c_longlong),('Flags',w.DWORD),('MinWorking',ctypes.c_size_t),('MaxWorking',ctypes.c_size_t),('Active',w.DWORD),('Affinity',ctypes.c_size_t),('Priority',w.DWORD),('Scheduling',w.DWORD)]
        class IO(ctypes.Structure):_fields_=[(n,ctypes.c_ulonglong) for n in ['ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes']]
        class Extended(ctypes.Structure):_fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];kernel.SetInformationJobObject.restype=w.BOOL
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.AssignProcessToJobObject.restype=w.BOOL
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
        handle=kernel.CreateJobObjectW(None,None);value=Extended();value.Basic.Flags=0x2000
        if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(value),ctypes.sizeof(value)) or not kernel.AssignProcessToJobObject(handle,w.HANDLE(int(process._handle))):
            if handle:kernel.CloseHandle(handle)
            kill_process_group(process);raise OSError('Windows process isolation job unavailable')
        self.handle=handle;self.kernel=kernel
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None
