#!/usr/bin/env bash
# Fake opt that emits non-empty but invalid IR (for finding-path tests).
set -uo pipefail
output=
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) output=${2:-}; shift 2;;
    -S|-passes=*|-stats) shift;;
    *) input=$1; shift;;
  esac
done
[[ -n "${output}" ]] || { echo "no -o" >&2; exit 1; }
if [[ "${output}" == "-" ]]; then
  printf '%s\n' 'this is not valid llvm ir INVALID'
else
  printf '%s\n' 'this is not valid llvm ir INVALID' > "${output}"
fi
