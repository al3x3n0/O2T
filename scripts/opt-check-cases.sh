#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: opt-check-cases.sh [--list-pipelines] [--require-observed-probes] [--alive2] [--alive2-bin PATH] CASES_DIR [passes]" >&2
  echo "example: opt-check-cases.sh klee-out/instcombine/<run>/cases instcombine,simplifycfg" >&2
  exit 2
fi

list_pipelines=0
require_observed_probes=0
run_alive2=0
alive2_bin=${O2T_ALIVE_TV:-${COMPILERVERIF_ALIVE_TV:-alive-tv}}
while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --list-pipelines)
      list_pipelines=1
      shift
      ;;
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
      echo "unknown option: $1" >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  echo "usage: opt-check-cases.sh [--list-pipelines] [--require-observed-probes] [--alive2] [--alive2-bin PATH] CASES_DIR [passes]" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "${script_dir}/.." && pwd)
workspace_root=$(cd "${root}/.." && pwd)
cases_input=$1
passes=${2:-}
image=${O2T_LLVM_IMAGE:-${COMPILERVERIF_LLVM_IMAGE:-silkeh/clang:18}}
platform=${O2T_DOCKER_PLATFORM:-${COMPILERVERIF_DOCKER_PLATFORM:-linux/amd64}}
replay="${root}/build/cv-replay"
probe_oracle="${root}/build/cv-probe-oracle"
semantic_checker="${root}/tools/cv-semantic-check-ir.py"
alive2_checker="${root}/tools/cv-alive2-check-ir.py"
semantic_clang=${O2T_SEMANTIC_CLANG:-${COMPILERVERIF_SEMANTIC_CLANG:-clang}}
host_opt=${O2T_HOST_OPT:-${COMPILERVERIF_HOST_OPT:-}}
host_llvm_as=${O2T_HOST_LLVM_AS:-${COMPILERVERIF_HOST_LLVM_AS:-}}

if [[ ! -x "${replay}" && "${list_pipelines}" -eq 0 ]]; then
  echo "cv-replay is not built at ${replay}" >&2
  echo "Run: cmake -S O2T -B O2T/build && cmake --build O2T/build" >&2
  exit 1
fi

if [[ ! -x "${probe_oracle}" && "${list_pipelines}" -eq 0 ]]; then
  echo "cv-probe-oracle is not built at ${probe_oracle}" >&2
  echo "Run: cmake -S O2T -B O2T/build && cmake --build O2T/build" >&2
  exit 1
fi

if [[ ! -x "${semantic_checker}" && "${list_pipelines}" -eq 0 ]]; then
  echo "semantic checker is not executable at ${semantic_checker}" >&2
  exit 1
fi

if [[ "${run_alive2}" -eq 1 && "${list_pipelines}" -eq 0 ]]; then
  if [[ ! -x "${alive2_checker}" ]]; then
    echo "Alive2 wrapper is not executable at ${alive2_checker}" >&2
    exit 1
  fi
  if ! command -v "${alive2_bin}" >/dev/null 2>&1; then
    echo "Alive2 executable not found: ${alive2_bin}" >&2
    echo "Set O2T_ALIVE_TV or pass --alive2-bin PATH." >&2
    exit 1
  fi
fi

if [[ "${list_pipelines}" -eq 0 && -z "${host_opt}" ]] && ! docker info >/dev/null 2>&1; then
  echo "Docker daemon unavailable. Start Docker and retry." >&2
  exit 1
fi

if [[ "${list_pipelines}" -eq 0 && ( -n "${host_opt}" || -n "${host_llvm_as}" ) ]]; then
  if [[ ! -x "${host_opt}" ]]; then
    echo "O2T_HOST_OPT is not executable: ${host_opt}" >&2
    exit 1
  fi
  if [[ ! -x "${host_llvm_as}" ]]; then
    echo "O2T_HOST_LLVM_AS is not executable: ${host_llvm_as}" >&2
    exit 1
  fi
fi

if [[ ! -d "${cases_input}" ]]; then
  echo "cases directory does not exist: ${cases_input}" >&2
  exit 1
fi

cases_dir=$(cd "${cases_input}" && pwd)
opt_dir="${cases_dir}/opt"
manifest="${opt_dir}/manifest.jsonl"
if [[ "${list_pipelines}" -eq 0 ]]; then
  mkdir -p "${opt_dir}"
  : > "${manifest}"
fi

shopt -s nullglob
configs=("${cases_dir}"/*.cfg)
if (( ${#configs[@]} == 0 )); then
  echo "no .cfg files found in ${cases_dir}" >&2
  exit 1
fi

failures=0

json_escape() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

config_value() {
  local config_path=$1
  local key=$2
  awk -F= -v key="${key}" '
    $1 == key {
      gsub(/[[:space:]]/, "", $2)
      print $2
      exit
    }
  ' "${config_path}"
}

shape_category() {
  local config_path=$1
  local memory_shape loop_shape shape vector_shape
  memory_shape=$(config_value "${config_path}" memory_shape)
  loop_shape=$(config_value "${config_path}" loop_shape)
  shape=$(config_value "${config_path}" shape)
  vector_shape=$(config_value "${config_path}" vector_shape)

  memory_shape=${memory_shape:-0}
  loop_shape=${loop_shape:-0}
  shape=${shape:-0}
  vector_shape=${vector_shape:-0}

  if [[ "${vector_shape}" != "0" ]]; then
    printf 'vector\n'
  elif [[ "${memory_shape}" != "0" ]]; then
    printf 'memory\n'
  elif [[ "${loop_shape}" != "0" ]]; then
    printf 'loop\n'
  elif [[ "${shape}" != "0" ]]; then
    printf 'cfg\n'
  else
    printf 'scalar\n'
  fi
}

pipeline_for_category() {
  case "$1" in
    scalar)
      printf 'instcombine\n'
      ;;
    cfg)
      printf 'simplifycfg,instcombine\n'
      ;;
    memory)
      printf 'mem2reg,dse,instcombine\n'
      ;;
    loop)
      printf 'loop-simplify,licm,indvars,simplifycfg,instcombine\n'
      ;;
    vector)
      printf 'instcombine\n'
      ;;
    *)
      printf 'instcombine\n'
      ;;
  esac
}

write_manifest() {
  local case_name=$1
  local config_path=$2
  local before_path=$3
  local after_path=$4
  local selected_passes=$5
  local category=$6
  local status=$7
  local message=$8
  local probe_log=${9:-}
  local expected_markers=${10:-}
  local observed_markers=${11:-}
  local oracle_status=${12:-}
  local missing_markers=${13:-}
  local unexpected_markers=${14:-}
  local semantic_status=${15:-}
  local semantic_sample_count=${16:-}
  local semantic_mismatch_input=${17:-}
  local semantic_before_output=${18:-}
  local semantic_after_output=${19:-}
  local semantic_message=${20:-}
  local alive2_status=${21:-not-run}
  local alive2_exit_code=${22:-}
  local alive2_message=${23:-}
  local alive2_output=${24:-}

  printf '{"case":%s,"config":%s,"before":%s,"after":%s,"passes":%s,"category":%s,"status":%s,"message":%s,"probe_log":%s,"expected_markers":%s,"observed_markers":%s,"oracle_status":%s,"missing_markers":%s,"unexpected_markers":%s,"semantic_status":%s,"semantic_sample_count":%s,"semantic_mismatch_input":%s,"semantic_before_output":%s,"semantic_after_output":%s,"semantic_message":%s,"alive2_status":%s,"alive2_exit_code":%s,"alive2_message":%s,"alive2_output":%s}\n' \
    "$(json_escape "${case_name}")" \
    "$(json_escape "${config_path}")" \
    "$(json_escape "${before_path}")" \
    "$(json_escape "${after_path}")" \
    "$(json_escape "${selected_passes}")" \
    "$(json_escape "${category}")" \
    "$(json_escape "${status}")" \
    "$(json_escape "${message}")" \
    "$(json_escape "${probe_log}")" \
    "$(json_escape "${expected_markers}")" \
    "$(json_escape "${observed_markers}")" \
    "$(json_escape "${oracle_status}")" \
    "$(json_escape "${missing_markers}")" \
    "$(json_escape "${unexpected_markers}")" \
    "$(json_escape "${semantic_status}")" \
    "$(json_escape "${semantic_sample_count}")" \
    "$(json_escape "${semantic_mismatch_input}")" \
    "$(json_escape "${semantic_before_output}")" \
    "$(json_escape "${semantic_after_output}")" \
    "$(json_escape "${semantic_message}")" \
    "$(json_escape "${alive2_status}")" \
    "$(json_escape "${alive2_exit_code}")" \
    "$(json_escape "${alive2_message}")" \
    "$(json_escape "${alive2_output}")" \
    >> "${manifest}"
}

kv_value() {
  local output=$1
  local key=$2
  awk -F= -v key="${key}" '$1 == key { print substr($0, length(key) + 2); exit }' "${output}"
}

json_value() {
  local output=$1
  local key=$2
  python3 -c 'import json, sys; data=json.load(open(sys.argv[1])); value=data.get(sys.argv[2], ""); print("" if value is None else value)' "${output}" "${key}"
}

run_semantic_check() {
  local before_path=$1
  local after_path=$2
  local work_dir=$3
  local output_path=$4

  "${semantic_checker}" \
    --before "${before_path}" \
    --after "${after_path}" \
    --work-dir "${work_dir}" \
    --clang "${semantic_clang}" \
    > "${output_path}"
}

run_alive2_check() {
  local before_path=$1
  local after_path=$2
  local result_path=$3
  local output_path=$4

  "${alive2_checker}" \
    --before "${before_path}" \
    --after "${after_path}" \
    --alive-tv "${alive2_bin}" \
    --out "${result_path}" \
    --output-log "${output_path}" \
    > /dev/null
}

run_container_opt() {
  local before_file=$1
  local after_file=$2
  local selected_passes=$3

  docker run \
    --rm \
    --platform "${platform}" \
    -u "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "${workspace_root}:/work" \
    -v "${cases_dir}:/cases" \
    -w /work/O2T \
    "${image}" \
    bash -lc "
set -euo pipefail

pick_tool() {
  for tool in \"\$@\"; do
    if command -v \"\${tool}\" >/dev/null 2>&1; then
      printf '%s\n' \"\${tool}\"
      return 0
    fi
  done
  return 1
}

llvm_as=\$(pick_tool llvm-as llvm-as-18 llvm-as-17 llvm-as-16 llvm-as-15 llvm-as-14) || {
  echo \"no llvm-as tool found in LLVM container\" >&2
  exit 1
}
opt_tool=\$(pick_tool opt opt-18 opt-17 opt-16 opt-15 opt-14) || {
  echo \"no opt tool found in LLVM container\" >&2
  exit 1
}

before=/cases/opt/${before_file}
after=/cases/opt/${after_file}

\"\${llvm_as}\" \"\${before}\" -o /dev/null
\"\${opt_tool}\" -S -passes=\"${selected_passes}\" \"\${before}\" -o \"\${after}\"
test -s \"\${after}\"
\"\${llvm_as}\" \"\${after}\" -o /dev/null
"
}

run_host_opt() {
  local before_file=$1
  local after_file=$2
  local selected_passes=$3
  local probe_log=$4
  local before="${opt_dir}/${before_file}"
  local after="${opt_dir}/${after_file}"

  : > "${probe_log}"
  "${host_llvm_as}" "${before}" -o /dev/null
  O2T_PASS_PROBE_LOG="${probe_log}" \
  COMPILERVERIF_PASS_PROBE_LOG="${probe_log}" \
    "${host_opt}" -S -passes="${selected_passes}" "${before}" -o "${after}"
  test -s "${after}"
  "${host_llvm_as}" "${after}" -o /dev/null
}

for config in "${configs[@]}"; do
  case_name=$(basename "${config}" .cfg)
  category=$(shape_category "${config}")
  selected_passes=${passes:-$(pipeline_for_category "${category}")}

  if [[ "${list_pipelines}" -eq 1 ]]; then
    printf '%s\t%s\t%s\n' "${case_name}" "${category}" "${selected_passes}"
    continue
  fi

  before_file="${case_name}.before.ll"
  after_file="${case_name}.after.ll"
  before_path="${opt_dir}/${before_file}"
  after_path="${opt_dir}/${after_file}"
  probe_log="${opt_dir}/${case_name}.probes.txt"
  oracle_output="${opt_dir}/${case_name}.oracle.txt"
  semantic_output="${opt_dir}/${case_name}.semantic.txt"
  semantic_work_dir="${opt_dir}/${case_name}.semantic-work"
  alive2_result="${opt_dir}/${case_name}.alive2.json"
  alive2_output="${opt_dir}/${case_name}.alive2.txt"

  if ! "${replay}" --config "${config}" --out "${before_path}"; then
    write_manifest "${case_name}" "${config}" "${before_path}" "${after_path}" "${selected_passes}" "${category}" "failed" "cv-replay failed"
    echo "${case_name}: cv-replay failed" >&2
    failures=$((failures + 1))
    continue
  fi

  if [[ -n "${host_opt}" ]]; then
    if run_host_opt "${before_file}" "${after_file}" "${selected_passes}" "${probe_log}"; then
      "${probe_oracle}" --config "${config}" --observed "${probe_log}" > "${oracle_output}"
      oracle_status=$(kv_value "${oracle_output}" oracle_status)
      probe_ok=1
      if [[ "${require_observed_probes}" -eq 1 && "${oracle_status}" != "matched" ]]; then
        probe_ok=0
      fi
      semantic_ok=0
      if run_semantic_check "${before_path}" "${after_path}" "${semantic_work_dir}" "${semantic_output}"; then
        semantic_ok=1
      fi
      alive2_ok=1
      alive2_status=not-run
      alive2_exit_code=
      alive2_message=
      alive2_output_record=
      if [[ "${run_alive2}" -eq 1 ]]; then
        if run_alive2_check "${before_path}" "${after_path}" "${alive2_result}" "${alive2_output}"; then
          :
        else
          alive2_ok=0
        fi
        alive2_status=$(json_value "${alive2_result}" alive2_status)
        alive2_exit_code=$(json_value "${alive2_result}" alive2_exit_code)
        alive2_message=$(json_value "${alive2_result}" alive2_message)
        alive2_output_record=$(json_value "${alive2_result}" alive2_output)
        if [[ "${alive2_status}" == "unsupported" ]]; then
          alive2_ok=1
        fi
      fi
      case_status=passed
      case_message=
      if [[ "${probe_ok}" -ne 1 ]]; then
        case_status=failed
        case_message="probe oracle failed"
      fi
      if [[ "${semantic_ok}" -ne 1 ]]; then
        case_status=failed
        if [[ -n "${case_message}" ]]; then
          case_message="${case_message}; semantic check failed"
        else
          case_message="semantic check failed"
        fi
      fi
      if [[ "${alive2_ok}" -ne 1 ]]; then
        case_status=failed
        if [[ -n "${case_message}" ]]; then
          case_message="${case_message}; Alive2 check failed"
        else
          case_message="Alive2 check failed"
        fi
      fi
      write_manifest \
        "${case_name}" "${config}" "${before_path}" "${after_path}" \
        "${selected_passes}" "${category}" \
        "${case_status}" \
        "${case_message}" \
        "${probe_log}" \
        "$(kv_value "${oracle_output}" expected_markers)" \
        "$(kv_value "${oracle_output}" observed_markers)" \
        "${oracle_status}" \
        "$(kv_value "${oracle_output}" missing_markers)" \
        "$(kv_value "${oracle_output}" unexpected_markers)" \
        "$(kv_value "${semantic_output}" semantic_status)" \
        "$(kv_value "${semantic_output}" sample_count)" \
        "$(kv_value "${semantic_output}" mismatch_input)" \
        "$(kv_value "${semantic_output}" before_output)" \
        "$(kv_value "${semantic_output}" after_output)" \
        "$(kv_value "${semantic_output}" message)" \
        "${alive2_status}" \
        "${alive2_exit_code}" \
        "${alive2_message}" \
        "${alive2_output_record}"
      if [[ "${probe_ok}" -ne 1 ]]; then
        echo "${case_name}: probe oracle failed (${oracle_status})" >&2
        failures=$((failures + 1))
      fi
      if [[ "${semantic_ok}" -ne 1 ]]; then
        echo "${case_name}: semantic check failed" >&2
        failures=$((failures + 1))
      fi
      if [[ "${alive2_ok}" -ne 1 ]]; then
        echo "${case_name}: Alive2 check failed (${alive2_status})" >&2
        failures=$((failures + 1))
      fi
    else
      write_manifest "${case_name}" "${config}" "${before_path}" "${after_path}" "${selected_passes}" "${category}" "failed" "host llvm-as or opt failed" "${probe_log}"
      echo "${case_name}: host llvm-as or opt failed" >&2
      failures=$((failures + 1))
    fi
  elif run_container_opt "${before_file}" "${after_file}" "${selected_passes}"; then
    semantic_ok=0
    if run_semantic_check "${before_path}" "${after_path}" "${semantic_work_dir}" "${semantic_output}"; then
      semantic_ok=1
    fi
    alive2_ok=1
    alive2_status=not-run
    alive2_exit_code=
    alive2_message=
    alive2_output_record=
    if [[ "${run_alive2}" -eq 1 ]]; then
      if run_alive2_check "${before_path}" "${after_path}" "${alive2_result}" "${alive2_output}"; then
        :
      else
        alive2_ok=0
      fi
      alive2_status=$(json_value "${alive2_result}" alive2_status)
      alive2_exit_code=$(json_value "${alive2_result}" alive2_exit_code)
      alive2_message=$(json_value "${alive2_result}" alive2_message)
      alive2_output_record=$(json_value "${alive2_result}" alive2_output)
      if [[ "${alive2_status}" == "unsupported" ]]; then
        alive2_ok=1
      fi
    fi
    case_status=passed
    case_message=
    if [[ "${semantic_ok}" -ne 1 ]]; then
      case_status=failed
      case_message="semantic check failed"
    fi
    if [[ "${alive2_ok}" -ne 1 ]]; then
      case_status=failed
      if [[ -n "${case_message}" ]]; then
        case_message="${case_message}; Alive2 check failed"
      else
        case_message="Alive2 check failed"
      fi
    fi
    write_manifest \
      "${case_name}" "${config}" "${before_path}" "${after_path}" \
      "${selected_passes}" "${category}" \
      "${case_status}" \
      "${case_message}" \
      "" "" "" "" "" "" \
      "$(kv_value "${semantic_output}" semantic_status)" \
      "$(kv_value "${semantic_output}" sample_count)" \
      "$(kv_value "${semantic_output}" mismatch_input)" \
      "$(kv_value "${semantic_output}" before_output)" \
      "$(kv_value "${semantic_output}" after_output)" \
      "$(kv_value "${semantic_output}" message)" \
      "${alive2_status}" \
      "${alive2_exit_code}" \
      "${alive2_message}" \
      "${alive2_output_record}"
    if [[ "${semantic_ok}" -ne 1 ]]; then
      echo "${case_name}: semantic check failed" >&2
      failures=$((failures + 1))
    fi
    if [[ "${alive2_ok}" -ne 1 ]]; then
      echo "${case_name}: Alive2 check failed (${alive2_status})" >&2
      failures=$((failures + 1))
    fi
  else
    write_manifest "${case_name}" "${config}" "${before_path}" "${after_path}" "${selected_passes}" "${category}" "failed" "llvm-as or opt failed"
    echo "${case_name}: llvm-as or opt failed" >&2
    failures=$((failures + 1))
  fi
done

if [[ "${list_pipelines}" -eq 1 ]]; then
  exit 0
fi

if (( failures > 0 )); then
  echo "${failures} case(s) failed; see ${manifest}" >&2
  exit 1
fi

echo "checked ${#configs[@]} case(s)"
echo "Outputs: ${opt_dir}"
echo "Manifest: ${manifest}"
