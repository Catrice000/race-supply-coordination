#!/usr/bin/env bash
# Build a wheel from any working directory. Requires Python 3.10+ and pip.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python}"
"$python_bin" -m pip wheel --no-deps --wheel-dir "$project_root/dist" "$project_root"
