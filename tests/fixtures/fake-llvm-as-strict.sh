#!/usr/bin/env bash
set -uo pipefail
input=${1:-}
if [[ "${input}" == "-" ]]; then
  data=$(cat)
  [[ -n "${data}" ]] || { echo "empty" >&2; exit 1; }
  printf '%s' "${data}" | grep -q INVALID && { echo "rejected INVALID" >&2; exit 1; }
  exit 0
fi
[[ -n "${input}" && -s "${input}" ]] || { echo "empty" >&2; exit 1; }
if grep -q INVALID "${input}"; then echo "rejected INVALID" >&2; exit 1; fi
exit 0
