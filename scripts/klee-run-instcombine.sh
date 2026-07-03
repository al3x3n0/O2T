#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "${script_dir}/.." && pwd)

exec python3 "${root}/tools/cv-run-klee-campaign.py" "$@"
