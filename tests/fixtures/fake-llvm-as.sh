#!/usr/bin/env bash
set -euo pipefail

input=${1:-}
if [[ -z "${input}" || ! -s "${input}" ]]; then
  echo "fake llvm-as: input is missing or empty: ${input}" >&2
  exit 1
fi
