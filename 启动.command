#!/bin/zsh
set -eu
ROOT="${0:A:h}"
cd "$ROOT"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3.13 >/dev/null 2>&1; then
  PY="$(command -v python3.13)"
else
  echo "请先安装 Python 3.13。说明见项目 docs/SETUP.md。"
  exit 1
fi
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  "$PY" "$ROOT/scripts/manage.py" install
fi
exec "$PY" "$ROOT/scripts/manage.py" serve "$@"
