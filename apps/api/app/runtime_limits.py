"""One memory-intensive worker per workbench; fail visibly instead of overcommitting RAM."""
import os
from contextlib import contextmanager
from .platform_support import exclusive_file


@contextmanager
def worker_slot(root):
    from .runner import SandboxError
    data = root/'data';path = data/'runtime-worker.lock'
    if data.is_symlink() or path.is_symlink():raise SandboxError('运行资源记录路径异常。')
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor = os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(descriptor,'r+b') as stream:
        guard=exclusive_file(stream,timeout=0)
        try:guard.__enter__()
        except TimeoutError:raise SandboxError('当前已有构建、浏览器检查或交互预览占用运行资源；请先关闭预览或等待当前检查结束，再手动重试。') from None
        try:yield
        finally:guard.__exit__(None,None,None)
