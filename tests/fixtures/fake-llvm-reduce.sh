#!/usr/bin/env bash
# Minimal stand-in for llvm-reduce: parse --test=/-o/input, exercise the
# interestingness test on the input, and emit a no-op "reduction" (copy).
set -uo pipefail
test_script= ; out= ; input=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --test=*) test_script="${1#--test=}"; shift;;
    --test-arg=*) shift;;
    -o) out="${2:-}"; shift 2;;
    *) input="$1"; shift;;
  esac
done
[[ -n "$test_script" && -n "$out" && -n "$input" ]] || { echo "fake llvm-reduce: missing args" >&2; exit 1; }
"$test_script" "$input" || { echo "fake llvm-reduce: input not interesting" >&2; exit 1; }
cp "$input" "$out"
