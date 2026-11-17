#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
conda_command="${CONDA_EXE:-conda}"
if ! command -v "$conda_command" >/dev/null 2>&1; then
    printf '%s\n' 'Conda was not found. Initialize Conda or add it to PATH.' >&2
    exit 1
fi
exec "$conda_command" run --no-capture-output -n bili python main.py
