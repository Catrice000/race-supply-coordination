#!/usr/bin/env bash
# Install a wheel into a NEW local directory. No hosting is configured.
set -euo pipefail
if [[ $# -ne 1 || -z "$1" ]]; then
  echo "Usage: bash scripts/deploy.sh <new-local-directory>" >&2
  exit 2
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python}"
target_dir="$1"
if [[ -e "$target_dir" ]]; then
  echo "Target already exists; choose a new directory." >&2
  exit 2
fi
shopt -s nullglob
wheels=("$project_root"/dist/race_supply_coordination-*.whl)
if [[ ${#wheels[@]} -ne 1 ]]; then
  echo "Expected one wheel in dist/. Run build.sh; remove obsolete wheels if needed." >&2
  exit 2
fi
"$python_bin" -m venv "$target_dir"
if [[ -f "$target_dir/bin/python" ]]; then
  runtime="$target_dir/bin/python"
else
  runtime="$target_dir/Scripts/python.exe"
fi
"$runtime" -m pip install --no-deps "${wheels[0]}"
"$runtime" -m race_supply.main --version
printf 'Installed locally. Run: "%s" -m race_supply.main --demo\n' "$runtime"
