#!/usr/bin/env bash
set -euo pipefail

execute=0
allow_dirty=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      execute=1
      shift
      ;;
    --allow-dirty)
      allow_dirty=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

command=${1:-}
shift || true

case "${command}" in
  check)
    echo "LLVM source: $1"
    echo "LLVM build: $2"
    ;;
  apply)
    echo "git apply $2"
    ;;
  configure)
    echo "cmake --build $2 --target opt llvm-as"
    ;;
  run-opt)
    alive2_status=not-run
    if [[ "${1:-}" == "--require-observed-probes" ]]; then
      shift
    fi
    if [[ "${1:-}" == "--alive2" ]]; then
      alive2_status=proved
      shift
    fi
    if [[ "${1:-}" == "--alive2-bin" ]]; then
      shift 2
    fi
    llvm_build=$1
    cases_dir=$2
    mkdir -p "${cases_dir}/opt"
    printf '%s\n' "{\"case\":\"klee_add_zero\",\"config\":\"klee_add_zero.cfg\",\"before\":\"before.ll\",\"after\":\"after.ll\",\"passes\":\"instcombine\",\"category\":\"scalar\",\"status\":\"passed\",\"message\":\"\",\"probe_log\":\"probe.log\",\"expected_markers\":\"probe.instcombine.add-zero\",\"observed_markers\":\"probe.instcombine.add-zero\",\"oracle_status\":\"matched\",\"missing_markers\":\"\",\"unexpected_markers\":\"\",\"semantic_status\":\"matched\",\"semantic_sample_count\":\"10\",\"semantic_mismatch_input\":\"\",\"semantic_before_output\":\"\",\"semantic_after_output\":\"\",\"semantic_message\":\"\",\"alive2_status\":\"${alive2_status}\",\"alive2_exit_code\":\"0\",\"alive2_message\":\"fake\",\"alive2_output\":\"alive2.txt\"}" > "${cases_dir}/opt/manifest.jsonl"
    echo "ran opt with ${llvm_build}"
    ;;
  *)
    echo "unknown command: ${command}" >&2
    exit 2
    ;;
esac

if [[ "${allow_dirty}" -eq 1 && "${execute}" -eq 1 ]]; then
  :
fi
