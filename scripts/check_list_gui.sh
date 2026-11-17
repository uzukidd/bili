#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
conda_command="${CONDA_EXE:-conda}"
exec "$conda_command" run --no-capture-output -n bili python -B scripts/check_list_gui.py "$@"
