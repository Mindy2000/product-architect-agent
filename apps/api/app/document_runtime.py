"""Local, demand-only document workers; no credentials passed to subprocesses."""
from __future__ import annotations

from .platform_support import exclusive_file, kill_process_group, WindowsJob
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'data' / 'ocr'
TESSDATA = DATA / 'tessdata'
MEMORY_BYTES = 768 * 1024 * 1024
TIMEOUT_SECONDS = 300
OCR_PAGES = 20
_thread_gate = threading.BoundedSemaphore(1)


class DocumentRuntimeError(Exception):
    pass


def capabilities():
    binary = shutil.which('tesseract')
    ready = bool(binary and all((TESSDATA / (lang + '.traineddata')).is_file() for lang in ('chi_sim', 'eng'))
                 and importlib.util.find_spec('pypdfium2') and importlib.util.find_spec('PIL'))
    return {'enabled': ready, 'engine': 'tesseract', 'local_only': True, 'languages': ['chi_sim', 'eng'],
            'concurrency': 1, 'memory_watchdog_mib': 768, 'timeout_seconds': TIMEOUT_SECONDS,
            'page_timeout_seconds': 30, 'max_ocr_pages': OCR_PAGES, 'render_dpi': 150,
            'max_render_pixels': 8000000, 'trigger': 'upload_or_explicit_reindex', 'paid_api': False}


def _kill(process):
    kill_process_group(process)


def extract_document(raw: bytes, suffix: str):
    # flock also covers multiple API processes. Locks release on process exit.
    if (ROOT / 'data').is_symlink() or DATA.is_symlink() or (DATA / 'worker.lock').is_symlink():
        raise DocumentRuntimeError('资料处理目录异常。')
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _thread_gate, (DATA / 'worker.lock').open('a+b') as lock:
        try:
            with exclusive_file(lock):return _run(raw,suffix)
        except TimeoutError:raise DocumentRuntimeError('本地解析任务繁忙，请稍后重试。') from None


def _run(raw, suffix):
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', OMP_THREAD_LIMIT='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               VECLIB_MAXIMUM_THREADS='1')
    with tempfile.TemporaryDirectory(prefix='document-', dir=DATA) as directory:
        work = Path(directory)
        env['TMPDIR'] = str(work)
        with (work / 'input').open('w+b') as source, (work / 'output').open('w+b') as output, (work / 'error').open('w+b') as error:
            source.write(raw); source.seek(0)
            process = subprocess.Popen([sys.executable, '-m', 'app.pdf_extract', suffix],
                                       stdin=source, stdout=output, stderr=error, cwd=ROOT / 'apps' / 'api',
                                       env=env, start_new_session=os.name!='nt', creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
            job=WindowsJob(process)
            started = time.monotonic()
            try:
                parent = psutil.Process(process.pid)
                while process.poll() is None:
                    if time.monotonic() - started > TIMEOUT_SECONDS:
                        raise DocumentRuntimeError('本地解析超过300秒，已停止整组处理进程。请拆分文件后重试。')
                    try:
                        members = [parent, *parent.children(recursive=True)]
                        rss = 0
                        for member in members:
                            try: rss += member.memory_info().rss
                            except psutil.NoSuchProcess: pass
                        if rss > MEMORY_BYTES:
                            raise DocumentRuntimeError('本地解析进程组超过768MiB内存保护线，已停止。请拆分文件后重试。')
                    except psutil.NoSuchProcess: pass
                    except psutil.AccessDenied:
                        raise DocumentRuntimeError('无法监测解析内存，已停止处理。')
                    if os.fstat(output.fileno()).st_size > 12 * 1024 * 1024 or os.fstat(error.fileno()).st_size > 1024 * 1024:
                        raise DocumentRuntimeError('解析输出超过保护上限，已停止处理。')
                    time.sleep(0.1)
                output.seek(0)
                try: value = json.loads(output.read(12 * 1024 * 1024 + 1))
                except (ValueError, UnicodeError):
                    raise DocumentRuntimeError('资料解析未完成或超过资源上限，请拆分后重试。')
                if process.returncode or value.get('error'):
                    messages = {'parser_unavailable': '本地解析依赖未安装，请重新安装项目依赖。',
                                'encrypted_pdf': '加密PDF暂不支持，请解除密码后上传。',
                                'page_limit': 'PDF超过200页，请拆分后上传。',
                                'ocr_unavailable': '本地OCR尚未就绪，请安装Tesseract及中英文语言包。',
                                'ocr_page_limit': '需要OCR的页面超过20页，请拆分后上传；此次未建立部分索引。',
                                'ocr_timeout': '单页OCR超过30秒，已停止处理，请拆分或降低扫描分辨率。',
                                'ocr_failed': '本地OCR未完成，请检查扫描文件或重新安装语言包。',
                                'office_limit': 'Office文件解压大小或条目超过上限，请拆分文件。',
                                'invalid_office': 'Office文件损坏、加密或格式不支持。',
                                'invalid_pdf': 'PDF无法解析或已损坏。'}
                    raise DocumentRuntimeError(messages.get(value.get('error'), '资料超过解析资源或文字上限，请拆分后上传。'))
                return value['pages']
            finally:
                # Kill descendants as well, including any Tesseract still running after worker exit.
                job.close()
                _kill(process)
