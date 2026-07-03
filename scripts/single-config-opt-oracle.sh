#!/usr/bin/env bash
# Single-config oracle for cv-reduce-failing-config.py.
#
# Runs opt-check-cases.sh on a throwaway directory containing only the candidate
# config and exits with the checker's status (non-zero when the case still
# fails). Use it from the reducer with --invert so a non-zero exit means "the
# failure still reproduces". The reducer passes the candidate config path as the
# sole argument (wire it as `--oracle 'single-config-opt-oracle.sh {cfg}'`).
#
# Configuration is taken from the environment so the oracle matches the campaign:
#   CV_OPT_CHECKER          path to opt-check-cases.sh (required)
#   CV_PASSES               opt pipeline (optional; otherwise per-shape default)
#   CV_ALIVE2=1             enable Alive2 checking
#   CV_ALIVE2_BIN           Alive2 executable
#   CV_REQUIRE_OBSERVED=1   require observed probe markers
#   O2T_HOST_OPT / O2T_HOST_LLVM_AS  host tools (read by the checker)
set -uo pipefail

config=${1:?usage: single-config-opt-oracle.sh CONFIG}
checker=${CV_OPT_CHECKER:?CV_OPT_CHECKER must point at opt-check-cases.sh}

tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
cp "${config}" "${tmp}/candidate.cfg"

flags=()
[[ "${CV_REQUIRE_OBSERVED:-0}" == "1" ]] && flags+=(--require-observed-probes)
[[ "${CV_ALIVE2:-0}" == "1" ]] && flags+=(--alive2)
[[ -n "${CV_ALIVE2_BIN:-}" ]] && flags+=(--alive2-bin "${CV_ALIVE2_BIN}")

# ${flags[@]+...} keeps `set -u` happy when no flags are set (bash 3.2).
if [[ -n "${CV_PASSES:-}" ]]; then
  "${checker}" ${flags[@]+"${flags[@]}"} "${tmp}" "${CV_PASSES}"
else
  "${checker}" ${flags[@]+"${flags[@]}"} "${tmp}"
fi
