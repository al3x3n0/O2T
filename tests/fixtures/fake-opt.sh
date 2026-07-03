#!/usr/bin/env bash
set -euo pipefail

input=
output=
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      output=${2:-}
      shift 2
      ;;
    -S|-passes=*)
      shift
      ;;
    *)
      input=$1
      shift
      ;;
  esac
done

if [[ -z "${input}" || -z "${output}" ]]; then
  echo "fake opt: expected input and -o output" >&2
  exit 1
fi

cp "${input}" "${output}"
probe_log=${O2T_PASS_PROBE_LOG:-${COMPILERVERIF_PASS_PROBE_LOG:-}}
if [[ -n "${probe_log}" ]]; then
  printf '%s\n' 'probe.instcombine.add-zero' >> "${probe_log}"
  printf '%s\n' 'probe.dce.dead-instruction' >> "${probe_log}"
fi
