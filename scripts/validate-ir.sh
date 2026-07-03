#!/usr/bin/env bash
# Validate generated LLVM IR with a local LLVM toolchain.
#
# For each config (or seed) this replays IR with cv-replay, parses it with
# llvm-as, and runs it through a representative optimization pipeline with opt.
# It is the local-toolchain counterpart to scripts/opt-check-cases.sh, used when
# the Docker LLVM image is unavailable (e.g. blocked registry). Point it at a
# Homebrew or system LLVM via CV_LLVM_BIN.
#
# Usage:
#   scripts/validate-ir.sh CONFIG [CONFIG...]      # validate one or more .cfg files
#   scripts/validate-ir.sh --seeds N               # validate configFromSeed 0..N-1
#   scripts/validate-ir.sh --dir DIR               # validate every *.cfg in DIR
#
# Environment:
#   CV_LLVM_BIN  directory containing llvm-as/opt
#                (default: /opt/homebrew/opt/llvm@18/bin, then PATH)
#   CV_PASSES    opt pipeline (default: a broad scalar+loop pipeline)
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
replay="${root}/build/cv-replay"

llvm_bin="${CV_LLVM_BIN:-/opt/homebrew/opt/llvm@18/bin}"
if [[ -x "${llvm_bin}/llvm-as" && -x "${llvm_bin}/opt" ]]; then
  llvm_as="${llvm_bin}/llvm-as"
  opt="${llvm_bin}/opt"
elif command -v llvm-as >/dev/null 2>&1 && command -v opt >/dev/null 2>&1; then
  llvm_as=$(command -v llvm-as)
  opt=$(command -v opt)
else
  echo "error: could not find llvm-as/opt (set CV_LLVM_BIN)" >&2
  exit 1
fi

passes="${CV_PASSES:-mem2reg,instcombine,simplifycfg,dse,loop-simplify,loop-mssa(licm)}"

if [[ ! -x "${replay}" ]]; then
  echo "error: cv-replay is not built at ${replay}" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT

pass=0
fail=0

validate_ll() {
  local label=$1 ll=$2
  if "${llvm_as}" "${ll}" -o /dev/null 2>"${tmp}/err" &&
     "${opt}" -S -passes="${passes}" "${ll}" -o /dev/null 2>>"${tmp}/err"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL ${label}"
    sed 's/^/    /' "${tmp}/err" | head -3
  fi
}

run_config() {
  local cfg=$1 ll="${tmp}/out.ll"
  "${replay}" --config "${cfg}" --out "${ll}" >/dev/null
  validate_ll "${cfg}" "${ll}"
}

run_seed() {
  local seed=$1 ll="${tmp}/out.ll"
  "${replay}" --seed "${seed}" --out "${ll}" >/dev/null
  validate_ll "seed=${seed}" "${ll}"
}

case "${1:-}" in
  --seeds)
    n=${2:?--seeds needs a count}
    for ((s = 0; s < n; s++)); do run_seed "${s}"; done
    ;;
  --dir)
    dir=${2:?--dir needs a path}
    shopt -s nullglob
    for cfg in "${dir}"/*.cfg; do run_config "${cfg}"; done
    ;;
  "")
    echo "usage: validate-ir.sh CONFIG... | --seeds N | --dir DIR" >&2
    exit 2
    ;;
  *)
    for cfg in "$@"; do run_config "${cfg}"; done
    ;;
esac

echo "validated with ${opt} (-passes=${passes})"
echo "PASS=${pass} FAIL=${fail}"
[[ ${fail} -eq 0 ]]
