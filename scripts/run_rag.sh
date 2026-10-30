#!/usr/bin/env bash
# Bash启动入口，切到根目录统一配置和缓存路径。
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
python_path="$project_root/.venv/bin/python"
if [[ ! -x "$python_path" ]]; then
  python_path="$project_root/.venv/Scripts/python.exe"
fi
if [[ ! -f "$python_path" ]]; then
  echo '请先在仓库根目录创建.venv，并安装requirements.txt。' >&2
  exit 1
fi
exec "$python_path" -m race_supply.phase1_rag "$@"
