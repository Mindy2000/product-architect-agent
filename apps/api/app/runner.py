"""Fixed, isolated React build and Playwright runner for generated projects."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from .process_control import run_bounded, OutputLimitExceeded
from .security_policy import redact, check_project_roots
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
GENERATED_ROOT = ROOT / "generated-projects"
OUTPUT_ROOT = ROOT / "outputs"
WORKER_CONTEXT = ROOT / "runtime" / "worker"
SECCOMP_PROFILE = ROOT / "runtime" / "seccomp-playwright.json"
WORKER_IMAGE = "product-architect-worker:1.0"
DOCKER_CONTEXT = os.getenv("PA_DOCKER_CONTEXT", "default" if os.name=="nt" else "colima")
if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",DOCKER_CONTEXT):raise ValueError("Docker context格式无效。")
MAX_FILE_BYTES = 24_000
MAX_TOTAL_BYTES = 72_000
ALLOWED_MODEL_PATHS = {"src/App.tsx", "src/main.tsx", "src/style.css", "tests/e2e.spec.ts"}


@dataclass
class CommandResult:
    exit_code: int
    output: str
    duration_seconds: float


class SandboxError(Exception):
    pass


def workspace_for(run_id: str) -> Path:
    try:
        normalized = str(uuid.UUID(run_id))
    except ValueError as exc:
        raise SandboxError("运行 ID 格式不正确。") from exc
    check_project_roots()
    if (GENERATED_ROOT/normalized).is_symlink():raise SandboxError("项目目录不允许符号链接。")
    root = GENERATED_ROOT.resolve()
    workspace = (root / normalized).resolve()
    if workspace.parent != root:
        raise SandboxError("项目路径不在受控工作区内。")
    return workspace


def _write_file(root: Path, rel: str, content: str | bytes) -> None:
    path = (root / rel).resolve()
    if root.resolve() not in path.parents:
        raise SandboxError("文件路径越出项目工作区。")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


BASE_FILES = {
    ".gitignore": "node_modules/\n.preview-storage.json\ndist/\ntest-results/\nplaywright-report/\n.DS_Store\n.env\n",
    "index.html": "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"UTF-8\"/><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"/><title>待办任务</title></head><body><div id=\"root\"></div><script type=\"module\" src=\"/src/main.tsx\"></script></body></html>\n",
    "src/main.tsx": "import React from 'react'\nimport { createRoot } from 'react-dom/client'\nimport App from './App'\nimport './style.css'\n\ncreateRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)\n",
    "src/vite-env.d.ts": "/// <reference types=\"vite/client\" />\n",
    "src/App.tsx": "export default function App(){return <main><h1>待办任务</h1><p>开始添加你的第一项任务。</p></main>}\n",
    "src/style.css": "@import \"tailwindcss\";\n:root{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;color:#1f2937;background:#f5f7fb}body{margin:0}\n",
    "vite.config.ts": "import {defineConfig} from 'vite'\nimport react from '@vitejs/plugin-react'\nimport tailwindcss from '@tailwindcss/vite'\nexport default defineConfig({plugins:[react(),tailwindcss()]})\n",
    "tsconfig.json": "{\"compilerOptions\":{\"target\":\"ES2022\",\"useDefineForClassFields\":true,\"lib\":[\"ES2022\",\"DOM\",\"DOM.Iterable\"],\"skipLibCheck\":true,\"esModuleInterop\":true,\"allowSyntheticDefaultImports\":true,\"strict\":true,\"module\":\"ESNext\",\"moduleResolution\":\"Bundler\",\"resolveJsonModule\":true,\"isolatedModules\":true,\"noEmit\":true,\"jsx\":\"react-jsx\"},\"include\":[\"src\"]}\n",
    "playwright.config.ts": "import {defineConfig,devices} from '@playwright/test'\nexport default defineConfig({testDir:'./tests',testIgnore:process.env.PHASE==='test'?['**/acceptance.spec.ts','**/preview.spec.ts']:[],fullyParallel:false,workers:1,timeout:30000,reporter:'line',use:{baseURL:'http://127.0.0.1:4173',trace:'retain-on-failure'},projects:[{name:'chromium',use:{...devices['Desktop Chrome']}}],webServer:{command:'npm run preview -- --host 127.0.0.1 --port 4173 --strictPort',url:'http://127.0.0.1:4173',reuseExistingServer:false,timeout:90000}})\n",
    "package.json": "",  # sourced from the locked worker package below
    "package-lock.json": "",  # sourced from the locked worker package below
}


def framework_files(run_id):
    from .fullstack import files
    return {**BASE_FILES,**files(run_id)}


def initialize_workspace(run_id: str, parent_run_id: str | None = None, *, application_type: str | None = None, crud_schema: dict | None = None) -> Path:
    workspace = workspace_for(run_id)
    from .project import metadata_dir, atomic_json
    pending = metadata_dir(run_id) / 'setup-pending.json'
    if workspace.exists() and not pending.exists():
        return workspace
    if not pending.exists(): atomic_json(pending, {'parent_run_id':parent_run_id})
    from . import fullstack
    mode=application_type or (fullstack.application_type(parent_run_id) if parent_run_id else 'frontend')
    setup=fullstack.prepare(run_id,mode,crud_schema)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o777)
    workspace.chmod(0o777)
    for name, content in framework_files(run_id).items():
        if name in {"package.json", "package-lock.json"}:
            source = WORKER_CONTEXT / name
            content = source.read_text(encoding="utf-8")
        _write_file(workspace, name, content)
    (workspace / "tests").mkdir(mode=0o777, exist_ok=True)
    (workspace / "tests").chmod(0o777)
    if mode=='fullstack_crud':
        _write_file(workspace,'backend/schema.json',json.dumps(setup['schema'],ensure_ascii=False,indent=2))
    from .project import import_sources, manifest
    if parent_run_id:
        parent_files=read_generated_sources(parent_run_id)
        if mode=='fullstack_crud':parent_files['backend/schema.json']=json.dumps(setup['schema'],ensure_ascii=False,indent=2)
        import_sources(run_id,parent_files)
    else:
        manifest(run_id)
    _git(workspace, ["init", "-q"])
    _git(workspace, ["config", "user.name", "Product Architect Agent"])
    _git(workspace, ["config", "user.email", "agent@localhost"])
    stage_project(run_id)
    try:
        _git(workspace, ['rev-parse','--verify','HEAD'])
        has_head = True
    except subprocess.CalledProcessError:
        has_head = False
    if not has_head or _git(workspace, ['status','--porcelain']).stdout.strip():
        _git(workspace, ["commit", "--quiet", "-m", "MVP starter template"])
    pending.unlink(missing_ok=True)
    return workspace


def ensure_vite_css_types(run_id: str) -> None:
    """Supply the standard Vite declaration needed for side-effect CSS imports."""
    workspace = workspace_for(run_id)
    if not workspace.exists() or workspace.is_symlink():
        raise SandboxError("任务工作区不存在或路径异常。")
    declaration = workspace / "src" / "vite-env.d.ts"
    if not declaration.exists():
        _write_file(workspace, "src/vite-env.d.ts", '/// <reference types="vite/client" />\n')


def ensure_preview_script(run_id: str) -> None:
    """Keep the isolated Playwright web server script in the fixed manifest."""
    workspace = workspace_for(run_id)
    if not workspace.exists() or workspace.is_symlink():
        raise SandboxError("任务工作区不存在或路径异常。")
    manifest = workspace / "package.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data.setdefault("scripts", {})["preview"] = "vite preview"
    _write_file(workspace, "package.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def disambiguate_priority_test_labels(run_id: str) -> bool:
    """Make Playwright's task-priority selector exact when a filter group shares its label."""
    workspace = workspace_for(run_id)
    if not workspace.exists() or workspace.is_symlink():
        raise SandboxError("任务工作区不存在或路径异常。")
    test_file = workspace / "tests" / "e2e.spec.ts"
    content = test_file.read_text(encoding="utf-8")
    updated = content.replace("getByLabel('优先级')", "getByLabel('优先级', { exact: true })")
    updated = updated.replace('getByLabel("优先级")', 'getByLabel("优先级", { exact: true })')
    if updated != content:
        apply_generated_files(run_id, [{"path":"tests/e2e.spec.ts","content":updated}])
        return True
    return False


def stabilize_toggle_test_locator(run_id: str) -> bool:
    """Avoid a dynamic accessible name invalidating a locator during checkbox toggling."""
    workspace = workspace_for(run_id)
    if not workspace.exists() or workspace.is_symlink():
        raise SandboxError("任务工作区不存在或路径异常。")
    test_file = workspace / "tests" / "e2e.spec.ts"
    content = test_file.read_text(encoding="utf-8")
    updated = content.replace(
        "  const checkbox = page.getByRole('checkbox', { name: '标记完成' });\n"
        "  await checkbox.check();\n"
        "  await expect(page.getByRole('checkbox', { name: '取消完成' })).toBeChecked();\n\n"
        "  await page.locator('input[type=\"checkbox\"]').uncheck();\n"
        "  await expect(page.getByRole('checkbox', { name: '标记完成' })).not.toBeChecked();",
        "  const checkbox = page.locator('input[type=\"checkbox\"]');\n"
        "  await checkbox.check();\n"
        "  await expect(checkbox).toBeChecked();\n\n"
        "  await checkbox.press('Space');\n"
        "  await expect(checkbox).not.toBeChecked();",
    )
    if updated != content:
        apply_generated_files(run_id, [{"path":"tests/e2e.spec.ts","content":updated}])
        return True
    return False


def _git(workspace: Path, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    # Git metadata is never mounted into a generated-code container.
    run_id = str(uuid.UUID(workspace.name))
    repositories = ROOT / "data" / "git"
    repositories.mkdir(parents=True, exist_ok=True)
    repository = repositories / run_id
    legacy = workspace / ".git"
    if not repository.exists() and legacy.is_dir():
        if legacy.is_symlink() or any(path.is_symlink() for path in legacy.rglob("*")):
            raise SandboxError("Git 元数据路径异常。")
        shutil.move(str(legacy), str(repository))
        (repository / "config").write_text("[core]\n repositoryformatversion = 0\n bare = false\n[user]\n name = Product Architect Agent\n email = agent@localhost\n")
        alternates = repository / "objects/info/alternates"
        if alternates.exists(): alternates.unlink()
    environment = {"GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":os.devnull, "GIT_ATTR_NOSYSTEM":"1"}
    command=["git","-c","core.hooksPath="+os.devnull,"-c","core.fsmonitor=false","--git-dir="+str(repository),"--work-tree="+str(workspace),*args]
    return run_bounded(command, cwd=workspace, env=environment, timeout=timeout, check=True)


def stage_project(run_id: str) -> None:
    from .project import manifest, safe_file
    workspace = workspace_for(run_id)
    names = set(manifest(run_id)['files']) | set(framework_files(run_id)) | {'tests/acceptance.spec.ts', 'tests/preview.spec.ts', 'preview.png'}
    paths = []
    for name in sorted(names):
        path = workspace / name
        if workspace not in path.resolve().parents or path.is_symlink():
            raise SandboxError('项目文件路径异常。')
        if path.is_file(): paths.append(name)
    _git(workspace, ['add', '-u'])
    if paths: _git(workspace, ['add', '--', *paths])


def apply_generated_files(run_id: str, files: list[dict[str, Any]], replace_all: bool = False) -> Path:
    """Compatibility adapter for V0.1 bundles and snapshot restoration."""
    from .project import sources, FileChange, apply_changes, digest
    before = sources(run_id)
    seen = set()
    changes = []
    for item in files:
        name, content = item.get('path'), item.get('content')
        if name in seen or not isinstance(name, str) or not isinstance(content, str):
            raise SandboxError('生成文件格式或路径重复。')
        seen.add(name)
        if before.get(name) == content: continue
        changes.append(FileChange(operation='replace' if name in before else 'create', path=name,
            expected_sha256=digest(before[name]) if name in before else None, content=content))
    if replace_all:
        for name in before.keys() - seen:
            changes.append(FileChange(operation='delete',path=name,expected_sha256=digest(before[name])))
    if changes: apply_changes(run_id, changes, 'bundle_' + uuid.uuid4().hex)
    return workspace_for(run_id)


def ensure_worker_image(timeout: int = 3600) -> str:
    image = run_bounded(["docker", "--context", DOCKER_CONTEXT, "image", "inspect", WORKER_IMAGE], timeout=30)
    if image.returncode == 0:
        return "worker 镜像已缓存"
    if os.getenv("WORKER_BASE_IMAGE_PULL_ACTIVE", "false").lower() == "true":
        base = "mcr.microsoft.com/playwright:v1.64.1-noble"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = run_bounded(["docker", "--context", DOCKER_CONTEXT, "image", "inspect", base], timeout=30)
            if ready.returncode == 0:
                break
            time.sleep(5)
        else:
            raise SandboxError("隔离运行基础镜像下载超时。")
    command = ["docker", "--context", DOCKER_CONTEXT, "build", "--pull", "--tag", WORKER_IMAGE, "--file", str(WORKER_CONTEXT / "Dockerfile"), str(WORKER_CONTEXT)]
    try:
        result = run_bounded(command, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("隔离运行镜像构建超时。") from exc
    if result.returncode:
        raise SandboxError("隔离运行镜像构建失败：" + _trim(result.stdout + "\n" + result.stderr))
    return _trim(result.stdout + "\n" + result.stderr)


def worker_image_available(image: str = WORKER_IMAGE) -> bool:
    try:
        result = run_bounded(["docker", "--context", DOCKER_CONTEXT, "image", "inspect", image], timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, OutputLimitExceeded):
        return False


def _trim(value: str, limit: int = 12_000) -> str:
    return redact(value)[-limit:]


def run_phase(run_id: str, phase: str, timeout: int = 150, *, parent_database: Path | None = None) -> CommandResult:
    if phase not in {"build", "test", "acceptance", "preview", "migrate"}:
        raise SandboxError("请求了未授权的容器命令。")
    workspace = workspace_for(run_id)
    if not workspace.exists() or workspace.is_symlink():
        raise SandboxError("项目工作区无效。")
    if not SECCOMP_PROFILE.is_file():
        raise SandboxError("Playwright 安全配置缺失。")
    fixed_script = "if [ ! -L node_modules ]; then ln -s /opt/worker/node_modules node_modules; fi; "
    fixed_script += 'if [ "$PHASE" = build ]; then npm run build; elif [ "$PHASE" = preview ]; then ./node_modules/.bin/playwright test tests/preview.spec.ts --reporter=line; elif [ "$PHASE" = acceptance ]; then ./node_modules/.bin/playwright test tests/acceptance.spec.ts --reporter=line; else npm run test:e2e; fi'
    from . import fullstack
    is_fullstack=fullstack.application_type(run_id)=='fullstack_crud'
    if phase=='migrate' and not is_fullstack:raise SandboxError('前端项目没有业务迁移命令。')
    if parent_database is not None and (not is_fullstack or phase!='migrate'):raise SandboxError('复制数据库只允许显式迁移。')
    if is_fullstack:fixed_script='if [ ! -L node_modules ]; then ln -s /opt/worker/node_modules node_modules; fi; '+fullstack.fixed_script(phase)
    if parent_database is not None:
        from .store import get_run
        state=get_run(run_id) or {}
        expected=fullstack.data_dir(state.get('parent_run_id'))
        if parent_database!=expected or parent_database.is_symlink():raise SandboxError('父数据库目录不匹配。')
        fixed_script='/opt/fullstack/venv/bin/python -m backend.copy_data && '+fixed_script
    from .container_owner import LABELS
    container_name="pa-phase-"+uuid.uuid4().hex
    command = [
        "docker", "--context", DOCKER_CONTEXT, "run", "--rm", "--init", "--name",container_name,*LABELS,
        "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        f"--security-opt=seccomp={SECCOMP_PROFILE}", "--pids-limit=128", "--memory=1536m", "--cpus=1.5", "--shm-size=384m",
        "--mount", f"type=bind,src={workspace},dst=/workspace",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
        "--workdir=/workspace", "--user=pwuser", "--env", "HOME=/tmp/worker-home",
        "--env", "PYTHONPYCACHEPREFIX=/tmp/pa-pycache", "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "npm_config_cache=/tmp/npm-cache", "--env", "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright",
        "--env", f"PHASE={phase}", "--entrypoint=/bin/bash", fullstack.IMAGE if is_fullstack else WORKER_IMAGE,
        "-lc", fixed_script,
    ]
    if is_fullstack:
        index=command.index('--workdir=/workspace')
        if phase=='migrate':command[index:index]=['--mount',f'type=bind,src={fullstack.data_dir(run_id)},dst=/appdata']
        if parent_database is not None:command[index:index]=['--mount',f'type=bind,src={parent_database},dst=/parentdata,readonly']
    started = time.monotonic()
    try:
        result = run_bounded(command, timeout=timeout)
        return CommandResult(result.returncode, _trim(result.stdout + "\n" + result.stderr), time.monotonic() - started)
    except (subprocess.TimeoutExpired, OutputLimitExceeded) as exc:
        try:run_bounded(["docker","--context",DOCKER_CONTEXT,"rm","-f",container_name],timeout=20)
        except (OSError, subprocess.SubprocessError):pass
        if isinstance(exc,OutputLimitExceeded):return CommandResult(125,str(exc),time.monotonic()-started)
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes): out = out.decode(errors="replace")
        if isinstance(err, bytes): err = err.decode(errors="replace")
        return CommandResult(124, _trim(str(out) + "\n" + str(err) + "\n命令超过时限，已终止容器。"), time.monotonic() - started)


def _source_files(workspace: Path) -> list[Path]:
    from .project import manifest
    names = set(manifest(workspace.name)['files']) | set(framework_files(workspace.name)) | {'tests/acceptance.spec.ts', 'tests/preview.spec.ts', 'preview.png'}
    result = []
    for name in sorted(names):
        path = workspace / name
        if workspace not in path.resolve().parents or path.is_symlink():
            raise SandboxError('导出路径异常。')
        if path.is_file(): result.append(path)
    return result


def capture_preview(run_id: str) -> CommandResult:
    workspace = workspace_for(run_id)
    _write_file(workspace, "tests/preview.spec.ts", "import { test } from '@playwright/test';\ntest('隔离页面预览', async ({page}) => { await page.goto('/'); await page.locator('#root').waitFor({state:'visible'}); await page.screenshot({path:'preview.png',fullPage:true}); });\n")
    return run_phase(run_id, "preview", timeout=90)


def deliver(run_id: str, state: dict[str, Any]) -> dict[str, str]:
    workspace = workspace_for(run_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stage_project(run_id)
    baseline = _git(workspace, ["rev-list", "--max-parents=0", "HEAD"]).stdout.strip()
    diff = _git(workspace, ["diff", "--no-ext-diff", "--no-textconv", "--cached", "--binary", baseline, "--"]).stdout
    diff_path = OUTPUT_ROOT / f"{run_id}-git-diff.patch"
    diff_path.write_text(diff, encoding="utf-8")
    trace_path = OUTPUT_ROOT / f"{run_id}-trace.json"
    trace_path.write_text(json.dumps(state.get("trace", []), ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = OUTPUT_ROOT / f"{run_id}-report.md"
    spec = state.get("product_spec", {})
    from .references import summaries
    reference_sources = summaries(state.get("reference_snapshot",[]))
    from .library import sources
    knowledge_sources = sources(state.get("knowledge_context"))
    from .research import summaries as web_summaries
    web_sources=web_summaries(state.get('web_snapshot',[]))
    from .model import budget_status
    run_cost = budget_status(run_id)["spent_cny_estimate"]
    repair_attempts = max(
        state.get("repair_round", 0),
        sum(1 for event in state.get("trace", []) if event.get("stage") == "repair" and event.get("status") == "running"),
    )
    report = [f"# {spec.get('product_name', '生成项目')} 交付报告", "", f"- 运行 ID：`{run_id}`", f"- 状态：`{state.get('status', 'unknown')}`", f"- 构建：`{state.get('build_status', 'unknown')}`", f"- Playwright：`{state.get('test_status', 'unknown')}`", f"- 自动修复尝试轮数：{repair_attempts} / 3", f"- 估算累计模型费用：¥{run_cost:.6f}", "", "## 产品摘要", "", spec.get("summary", ""), "", "## 验收标准", ""]
    if spec.get('application_type')=='fullstack_crud':
        report.extend(['','## 业务后端与数据','','- FastAPI + SQLite，受控CRUD数据结构生成。','- 业务数据库按项目保存，不包含在源码ZIP或源码版本恢复内。','- 测试使用临时数据库；真实项目库初始化/升级需显式执行。'])
    if state.get('runtime_version',1) >= 2:
        report.extend(['', '## 任务与执行', '', f"- 决策步数：{state.get('executor_turns',0)} / {state.get('execution_step_limit',40)}", f"- 本次估算费用上限：¥{state.get('run_budget_cny',5)}"])
        report.extend(f"- {task['id']} [{task['status']}] {task['title']}；依赖：{', '.join(task.get('depends_on',[])) or '无'}；修改：{', '.join(task.get('changed_paths',[])) or '无'}" for task in state.get('executor_tasks',[]))
        report.extend(['', '## 验收标准', ''])
    if state.get('operator_assisted'):
        report.extend(['', '## 执行方式与限制', '', state.get('operator_note','本次由Codex辅助完成，不能视为执行器完全自主完成。'), '', '## 验收标准', ''])
    report.extend(f"- {item}" for item in state.get("acceptance_criteria", []))
    if web_sources:
        report.extend(['','## 公开网页参考来源（冻结快照，非验收证明）',''])
        report.extend('- '+item['title']+' · '+item['url']+' · SHA256 '+item['sha256'] for item in web_sources)
    report.extend(["", "## 浏览器验收结果", "", _trim(state.get("test_output", "未运行"), 8000), "", "## 构建结果", "", _trim(state.get("build_output", "未运行"), 8000), "", "## 自动修复记录", ""])
    report.extend(f"- {event.get('summary', '')}" for event in state.get("trace", []) if event.get("stage") == "repair")
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    archive_path = OUTPUT_ROOT / f"{run_id}-source.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        if state.get('requirements_confirmed'):
            archive.writestr('PRODUCT_SPEC.json',json.dumps({'design_id':state.get('design_id'),'revision':state.get('design_revision'),'spec':spec,'reference_sources':reference_sources,'knowledge_sources':knowledge_sources,'web_sources':web_sources},ensure_ascii=False,indent=2))
        for path in _source_files(workspace):
            archive.write(path, path.relative_to(workspace).as_posix())
    relative_workspace = workspace.relative_to(ROOT).as_posix()
    from .project import manifest
    manifest_path = OUTPUT_ROOT / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest(run_id),ensure_ascii=False,indent=2),encoding="utf-8")
    paths = {"manifest":manifest_path.relative_to(ROOT).as_posix(),"workspace": relative_workspace, "source_zip": archive_path.relative_to(ROOT).as_posix(), "git_diff": diff_path.relative_to(ROOT).as_posix(), "report": report_path.relative_to(ROOT).as_posix(), "trace": trace_path.relative_to(ROOT).as_posix()}
    if state.get('requirements_confirmed'):
        specification_path = OUTPUT_ROOT / f"{run_id}-product-spec.json"
        specification_path.write_text(json.dumps({'design_id':state.get('design_id'),'revision':state.get('design_revision'),'spec':spec,'reference_sources':reference_sources,'knowledge_sources':knowledge_sources,'web_sources':web_sources},ensure_ascii=False,indent=2),encoding='utf-8')
        paths['product_spec'] = specification_path.relative_to(ROOT).as_posix()
    preview = workspace / "preview.png"
    if preview.is_file() and not preview.is_symlink():
        target = OUTPUT_ROOT / f"{run_id}-preview.png"
        shutil.copyfile(preview, target)
        paths["preview"] = target.relative_to(ROOT).as_posix()
    return paths


def read_generated_sources(run_id: str) -> dict[str, str]:
    from .project import sources
    return sources(run_id)


def version_snapshots(run_id: str) -> list[dict]:
    workspace = workspace_for(run_id)
    if (not (workspace / ".git").is_dir() and not (ROOT / "data/git" / run_id).is_dir()) or workspace.is_symlink():
        return []
    revisions = _git(workspace, ["log", "--format=%H%x09%aI%x09%s"]).stdout.splitlines()
    result=[]
    for line in revisions:
        sha, at, title=line.split("\t",2)
        names=set(_git(workspace,["ls-tree","-r","--name-only",sha]).stdout.splitlines())
        if {'src/App.tsx','src/main.tsx','src/style.css'} <= names and any(name.startswith('tests/') and name.endswith('.spec.ts') and name not in {'tests/acceptance.spec.ts','tests/preview.spec.ts'} for name in names):
            result.append({"commit":sha,"at":at,"title":title})
    return result


def read_source_snapshot(run_id: str, commit: str) -> list[dict]:
    if not re.fullmatch(r"[0-9a-f]{40}",commit):
        raise SandboxError("版本标识格式无效。")
    if commit not in {item["commit"] for item in version_snapshots(run_id)}:
        raise SandboxError("该版本不包含完整源码或不属于本项目。")
    workspace=workspace_for(run_id)
    from .project import managed_path, validate_sources
    names = _git(workspace,["ls-tree","-r","--name-only",commit]).stdout.splitlines()
    files = {name:_git(workspace,["show",f"{commit}:{name}"]).stdout for name in names if managed_path(name)}
    validate_sources(files)
    return [{"path":name,"content":content} for name,content in sorted(files.items())]
