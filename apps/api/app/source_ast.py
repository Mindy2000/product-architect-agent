"""Optional local AST parser. User source is input text, never executable code."""
import json
import shutil
import tempfile
import threading
from pathlib import Path
from .process_control import run_bounded


_GATE = threading.BoundedSemaphore(1)

def locate(files, selection):
    root = Path(__file__).resolve().parents[3]
    if not shutil.which('node') or not (root/'node_modules/rolldown').is_dir():
        return {'available': False, 'reason': '未安装本地语法分析依赖，使用文字候选；不会自动安装。'}
    if len(files) > 128 or sum(len(body.encode()) for body in files.values()) > 2*1024**2:
        return {'available': False, 'reason': '源码超出分析上限，使用受限文字候选。'}
    if not _GATE.acquire(timeout=1):
        return {'available': False, 'reason': '语法分析正在处理其他请求，当前保留文字候选。'}
    try:
        with tempfile.TemporaryDirectory(prefix='pa-source-analysis-') as directory:
            path = Path(directory)/'input.json'
            path.write_text(json.dumps({'files': files, 'selection': selection}, ensure_ascii=False))
            path.chmod(0o600)
            result = run_bounded(['node', '--max-old-space-size=256', str(root/'scripts/source_ast.cjs'), str(path)],
                                 cwd=root, timeout=8, max_output_bytes=128*1024)
            if result.returncode:
                return {'available': False, 'reason': '语法分析未完成，保留文字候选；未执行源码。'}
            return {'available': True, **json.loads(result.stdout)}
    except Exception:
        return {'available': False, 'reason': '语法分析暂不可用，保留文字候选；未执行源码。'}

    finally:
        _GATE.release()
