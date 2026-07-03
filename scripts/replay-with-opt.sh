#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: replay-with-opt.sh CONFIG [passes]" >&2
  echo "example: replay-with-opt.sh examples/add_zero.cfg instcombine,simplifycfg" >&2
  exit 2
fi

config=$1
passes=${2:-instcombine}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
replay="${root}/build/cv-replay"

if [[ ! -x "${replay}" ]]; then
  echo "cv-replay is not built at ${replay}" >&2
  exit 1
fi

if ! command -v opt >/dev/null 2>&1; then
  echo "opt is not on PATH" >&2
  exit 1
fi

if ! command -v llvm-as >/dev/null 2>&1; then
  echo "llvm-as is not on PATH" >&2
  exit 1
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "${tmpdir}"' EXIT

input_ll="${tmpdir}/input.ll"
output_ll="${tmpdir}/output.ll"

"${replay}" --config "${config}" --out "${input_ll}"
llvm-as "${input_ll}" -o /dev/null
opt -S -passes="${passes}" "${input_ll}" -o "${output_ll}"
llvm-as "${output_ll}" -o /dev/null

cat "${output_ll}"
