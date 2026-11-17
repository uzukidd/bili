#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
conda_command="${CONDA_EXE:-conda}"
if ! command -v "$conda_command" >/dev/null 2>&1; then
    printf '%s\n' 'Conda was not found. Initialize Conda or add it to PATH.' >&2
    exit 1
fi
environments="$("$conda_command" env list)"
if printf '%s\n' "$environments" | grep -Eq '^bili[[:space:]]'; then
    "$conda_command" install --yes --name bili python=3.12
else
    "$conda_command" create --yes --name bili python=3.12
fi
"$conda_command" run --no-capture-output -n bili python -m pip install -r requirements.txt
