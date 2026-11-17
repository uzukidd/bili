#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
conda_command="${CONDA_EXE:-conda}"
if ! command -v "$conda_command" >/dev/null 2>&1; then
    printf '%s\n' 'Conda was not found. Initialize Conda or set CONDA_EXE.' >&2
    exit 1
fi
if ! "$conda_command" run --no-capture-output -n bili python --version; then
    printf '%s\n' 'Run bash scripts/setup.sh first to create the bili environment.' >&2
    exit 1
fi
"$conda_command" run --no-capture-output -n bili python -m pip install -r requirements-build.txt
exec "$conda_command" run --no-capture-output -n bili python -m bili_player.packaging deploy "$@"
