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
  echo "fake no-probe opt: expected input and -o output" >&2
  exit 1
fi

cp "${input}" "${output}"
