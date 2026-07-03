#!/usr/bin/env bash
# A deliberately "miscompiling" optimizer: clears the low bit of the exit-code
# fold, changing the program's observable result -- to prove the execution
# differential detects (and minimizes) a real behavioral divergence.
set -uo pipefail
out= ; input=
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out=${2:-}; shift 2;;
    -S|-passes=*|-stats) shift;;
    *) input=$1; shift;;
  esac
done
[[ -n "$out" ]] || { echo "no -o" >&2; exit 1; }
sed 's/and i32 %m.x2, 255/and i32 %m.x2, 254/' "$input" > "$out"
