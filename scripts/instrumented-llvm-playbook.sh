#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: instrumented-llvm-playbook.sh [--execute] [--allow-dirty] COMMAND ...

commands:
  check LLVM_SRC LLVM_BUILD
  apply LLVM_SRC PATCH
  configure LLVM_SRC LLVM_BUILD
  run-opt [--require-observed-probes] [--alive2] [--alive2-bin PATH] LLVM_BUILD CASES_DIR

By default commands are printed or validated only. Use --execute to run
side-effecting apply/configure/run-opt actions.
EOF
}

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
    --help|-h)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

command=$1
shift

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "${script_dir}/.." && pwd)
replay="${root}/build/cv-replay"
opt_checker="${root}/scripts/opt-check-cases.sh"

quote() {
  printf '%q' "$1"
}

print_command() {
  local first=1
  for arg in "$@"; do
    if [[ "${first}" -eq 0 ]]; then
      printf ' '
    fi
    quote "${arg}"
    first=0
  done
  printf '\n'
}

require_path() {
  local path=$1
  local label=$2
  if [[ ! -e "${path}" ]]; then
    echo "${label} does not exist: ${path}" >&2
    exit 1
  fi
}

require_dir() {
  local path=$1
  local label=$2
  if [[ ! -d "${path}" ]]; then
    echo "${label} is not a directory: ${path}" >&2
    exit 1
  fi
}

ensure_clean_git() {
  local llvm_src=$1
  if [[ "${allow_dirty}" -eq 1 ]]; then
    return
  fi
  if [[ ! -d "${llvm_src}/.git" ]]; then
    echo "not a git checkout, refusing without --allow-dirty: ${llvm_src}" >&2
    exit 1
  fi
  if ! git -C "${llvm_src}" diff --quiet || ! git -C "${llvm_src}" diff --cached --quiet; then
    echo "LLVM checkout has uncommitted changes; use --allow-dirty to continue" >&2
    exit 1
  fi
}

check_layout() {
  local llvm_src=$1
  local llvm_build=$2
  require_dir "${llvm_src}" "LLVM source"
  require_dir "${llvm_build}" "LLVM build"

  if [[ ! -f "${llvm_build}/compile_commands.json" ]]; then
    echo "warning: missing ${llvm_build}/compile_commands.json" >&2
  fi
  if [[ ! -x "${llvm_build}/bin/opt" ]]; then
    echo "warning: missing executable ${llvm_build}/bin/opt" >&2
  fi
  if [[ ! -x "${llvm_build}/bin/llvm-as" ]]; then
    echo "warning: missing executable ${llvm_build}/bin/llvm-as" >&2
  fi

  echo "LLVM source: ${llvm_src}"
  echo "LLVM build: ${llvm_build}"
}

run_apply() {
  local llvm_src=$1
  local patch=$2
  require_dir "${llvm_src}" "LLVM source"
  require_path "${patch}" "instrumentation patch"
  ensure_clean_git "${llvm_src}"

  if [[ "${execute}" -eq 1 ]]; then
    git -C "${llvm_src}" apply "${patch}"
  else
    print_command git -C "${llvm_src}" apply "${patch}"
  fi
}

run_configure() {
  local llvm_src=$1
  local llvm_build=$2
  require_dir "${llvm_src}" "LLVM source"

  local cmake_cmd=(
    cmake
    -S "${llvm_src}/llvm"
    -B "${llvm_build}"
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DLLVM_ENABLE_PROJECTS=
    -DLLVM_TARGETS_TO_BUILD=Native
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  )
  local build_cmd=(cmake --build "${llvm_build}" --target opt llvm-as)

  if [[ "${execute}" -eq 1 ]]; then
    mkdir -p "${llvm_build}"
    "${cmake_cmd[@]}"
    "${build_cmd[@]}"
  else
    print_command "${cmake_cmd[@]}"
    print_command "${build_cmd[@]}"
  fi
}

run_cases() {
  local require_observed_probes=0
  local run_alive2=0
  local alive2_bin=
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --require-observed-probes)
        require_observed_probes=1
        shift
        ;;
      --alive2)
        run_alive2=1
        shift
        ;;
      --alive2-bin)
        alive2_bin=${2:-}
        if [[ -z "${alive2_bin}" ]]; then
          echo "--alive2-bin requires a path" >&2
          exit 2
        fi
        shift 2
        ;;
      --)
        shift
        break
        ;;
      --*)
        echo "unknown run-opt option: $1" >&2
        exit 2
        ;;
      *)
        break
        ;;
    esac
  done
  if [[ $# -ne 2 ]]; then
    usage
    exit 2
  fi
  local llvm_build=$1
  local cases_dir=$2
  require_dir "${llvm_build}" "LLVM build"
  require_dir "${cases_dir}" "cases directory"
  require_path "${opt_checker}" "opt checker"
  if [[ ! -x "${replay}" ]]; then
    echo "warning: cv-replay is not built at ${replay}" >&2
  fi

  local opt_tool="${llvm_build}/bin/opt"
  local llvm_as="${llvm_build}/bin/llvm-as"
  local checker_cmd=("${opt_checker}")
  if [[ "${require_observed_probes}" -eq 1 ]]; then
    checker_cmd+=(--require-observed-probes)
  fi
  if [[ "${run_alive2}" -eq 1 ]]; then
    checker_cmd+=(--alive2)
  fi
  if [[ -n "${alive2_bin}" ]]; then
    checker_cmd+=(--alive2-bin "${alive2_bin}")
  fi
  checker_cmd+=("${cases_dir}")
  if [[ "${execute}" -eq 1 ]]; then
    if [[ ! -x "${opt_tool}" || ! -x "${llvm_as}" ]]; then
      echo "instrumented opt/llvm-as missing under ${llvm_build}/bin" >&2
      exit 1
    fi
    O2T_HOST_OPT="${opt_tool}" \
      O2T_HOST_LLVM_AS="${llvm_as}" \
      COMPILERVERIF_HOST_OPT="${opt_tool}" \
      COMPILERVERIF_HOST_LLVM_AS="${llvm_as}" \
      "${checker_cmd[@]}"
  else
    printf 'O2T_HOST_OPT=%q O2T_HOST_LLVM_AS=%q ' "${opt_tool}" "${llvm_as}"
    print_command "${checker_cmd[@]}"
  fi
}

case "${command}" in
  check)
    if [[ $# -ne 2 ]]; then
      usage
      exit 2
    fi
    check_layout "$1" "$2"
    ;;
  apply)
    if [[ $# -ne 2 ]]; then
      usage
      exit 2
    fi
    run_apply "$1" "$2"
    ;;
  configure)
    if [[ $# -ne 2 ]]; then
      usage
      exit 2
    fi
    run_configure "$1" "$2"
    ;;
  run-opt)
    run_cases "$@"
    ;;
  *)
    echo "unknown command: ${command}" >&2
    usage
    exit 2
    ;;
esac
