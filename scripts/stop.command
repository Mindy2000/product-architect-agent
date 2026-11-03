#!/bin/zsh
set -eu
umask 077

ROOT="${0:A:h:h}"

stop_managed_process() {
  local pid_file="$1"
  local marker="$2"
  if [[ ! -f "$pid_file" ]]; then return; fi
  local pid
  pid="$(<"$pid_file")"
  if [[ "$pid" == <-> ]]; then
    local command_line process_cwd
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    if [[ -n "$command_line" && "$command_line" == *"$marker"* && ( "$process_cwd" == "$ROOT" || "$process_cwd" == "$ROOT/apps/web" ) ]]; then
      kill "$pid" 2>/dev/null || true
      echo "已停止进程 $pid。"
    elif [[ -n "$command_line" ]]; then
      echo "进程不属于当前项目，已跳过并保留记录。"
      return
    fi
  fi
  rm -f "$pid_file"
}

stop_managed_process "$ROOT/data/web.pid" "node_modules/.bin/vite"
stop_managed_process "$ROOT/data/api.pid" "uvicorn app.main:app"
echo "已发送停止请求。Colima 隔离环境保持运行，以便下次更快启动。"
