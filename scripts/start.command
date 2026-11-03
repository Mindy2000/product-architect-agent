#!/bin/zsh
set -eu
umask 077

ROOT="${0:A:h:h}"
cd "$ROOT"
mkdir -p data
managed_pids=()
cleanup() {
  for managed_pid in "${managed_pids[@]}"; do
    local cleanup_command cleanup_cwd
    cleanup_command="$(ps -p "$managed_pid" -o command= 2>/dev/null || true)"
    cleanup_cwd="$(lsof -a -p "$managed_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    if [[ ( "$cleanup_cwd" == "$ROOT" || "$cleanup_cwd" == "$ROOT/apps/web" ) && ( "$cleanup_command" == *"uvicorn app.main:app"* || "$cleanup_command" == *"node_modules/.bin/vite"* ) ]]; then
      kill "$managed_pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM HUP

record_project_process() {
  local port="$1"
  local marker="$2"
  local name="$3"
  local pid
  pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ "$pid" == <-> ]]; then
    local command_line process_cwd
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    if [[ "$command_line" == *"$marker"* && ( "$process_cwd" == "$ROOT" || "$process_cwd" == "$ROOT/apps/web" ) ]]; then
      echo "$pid" > "$ROOT/data/${name}.pid"
    else
      echo "该端口不属于当前项目，已停止启动。"
      exit 1
    fi
  fi
}

if ! command -v colima >/dev/null 2>&1; then
  echo "未找到 Colima。请先安装项目依赖后重试。"
  exit 1
fi

if ! docker --context colima info >/dev/null 2>&1; then
  echo "正在启动本地隔离运行环境……"
  colima start --runtime docker --vm-type vz --cpu 2 --memory 2 --disk 12
fi

if ! docker --context colima image inspect product-architect-worker:1.0 >/dev/null 2>&1; then
  echo "正在准备 Playwright 隔离镜像……"
  docker --context colima build --tag product-architect-worker:1.0 --file runtime/worker/Dockerfile runtime/worker
fi

if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "本地 API 已在运行。"
else
  if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 8000 已被其他程序占用；为避免影响它，启动已停止。"
    exit 1
  fi
  nohup "$ROOT/.venv/bin/uvicorn" app.main:app --app-dir "$ROOT/apps/api" --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log --limit-concurrency 64 --backlog 64 --timeout-keep-alive 5 --timeout-graceful-shutdown 15 > "$ROOT/data/api.log" 2>&1 < /dev/null &
  echo $! > "$ROOT/data/api.pid"
  managed_pids+=("$(<"$ROOT/data/api.pid")")
  for attempt in {1..30}; do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "API 启动失败，请查看 data/api.log。"
    exit 1
  fi
fi
record_project_process 8000 "uvicorn app.main:app" api

if curl -fsS http://127.0.0.1:5173/ 2>/dev/null | grep -q "Product Architect Agent"; then
  echo "中文工作台已在运行。"
else
  if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 5173 已被其他程序占用；为避免影响它，启动已停止。"
    exit 1
  fi
  cd "$ROOT/apps/web"
  nohup "$ROOT/node_modules/.bin/vite" --host 127.0.0.1 > "$ROOT/data/web.log" 2>&1 < /dev/null &
  echo $! > "$ROOT/data/web.pid"
  managed_pids+=("$(<"$ROOT/data/web.pid")")
  cd "$ROOT"
  for attempt in {1..30}; do
    if curl -fsS http://127.0.0.1:5173/ 2>/dev/null | grep -q "Product Architect Agent"; then break; fi
    sleep 1
  done
  if ! curl -fsS http://127.0.0.1:5173/ 2>/dev/null | grep -q "Product Architect Agent"; then
    echo "工作台启动失败，请查看 data/web.log。"
    exit 1
  fi
fi
record_project_process 5173 "node_modules/.bin/vite" web

echo "Product Architect Agent 已就绪：http://127.0.0.1:5173/"
if [[ "${1:-}" != "--no-open" ]]; then
  open http://127.0.0.1:5173/
fi

if (( ${#managed_pids[@]} > 0 )); then
  echo "请保持此启动窗口打开。使用停止脚本或关闭此窗口即可停止本次启动的服务。"
  wait "${managed_pids[@]}"
fi
