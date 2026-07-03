#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "${script_dir}/.." && pwd)

python_bin=${PYTHON3:-python3}
z3_bin=${Z3:-}
out_dir=${O2T_GLOBALOPT_STRICT_OUT:-${COMPILERVERIF_GLOBALOPT_STRICT_OUT:-"${root}/build-clang-tools/globalopt-strict-check"}}
keep_output=0
clean_only=0

usage() {
  cat >&2 <<EOF
usage: globalopt-strict-check.sh [--out DIR] [--z3 PATH] [--keep-output] [--clean]

Runs the strict GlobalOpt dead-initializer regression harness:
  - focused coverage and rewrite provenance
  - typed initializer witnesses
  - campaign/evidence/audit/promotion propagation
  - workflow coverage feed-through

By default, generated output is removed after a successful run and preserved on
failure for debugging.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out_dir=${2:-}
      if [[ -z "${out_dir}" ]]; then
        echo "--out requires a directory" >&2
        exit 2
      fi
      shift 2
      ;;
    --z3)
      z3_bin=${2:-}
      if [[ -z "${z3_bin}" ]]; then
        echo "--z3 requires a path" >&2
        exit 2
      fi
      shift 2
      ;;
    --keep-output)
      keep_output=1
      shift
      ;;
    --clean)
      clean_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

clean_outputs() {
  rm -rf "${out_dir}"
  find "${root}/tools" "${root}/tests/fixtures" -type d -name __pycache__ -prune -exec rm -rf {} +
}

if [[ "${clean_only}" -eq 1 ]]; then
  clean_outputs
  exit 0
fi

if [[ -z "${z3_bin}" ]]; then
  z3_bin=$(command -v z3 || true)
fi
if [[ -z "${z3_bin}" || ! -x "${z3_bin}" ]]; then
  echo "z3 executable not found; set Z3 or pass --z3 PATH" >&2
  exit 1
fi

clean_outputs
mkdir -p "${out_dir}"

echo "python syntax checks"
"${python_bin}" -m py_compile \
  "${root}/tools/cv-run-globalopt-coverage.py" \
  "${root}/tools/cv-build-intent-evidence.py" \
  "${root}/tools/cv-verify-globalopt-witness-contract.py" \
  "${root}/tools/cv-verify-predicate-provenance.py" \
  "${root}/tools/cv-audit-intent-coverage.py" \
  "${root}/tools/cv-promote-intent-candidates.py" \
  "${root}/tools/cv-run-campaign.py" \
  "${root}/tools/cv-run-verification-workflow.py" \
  "${root}/tools/cv_formal_ir.py" \
  "${root}/tools/cv-validate-intent-registry.py" \
  "${root}/tests/fixtures/globalopt_coverage_fixture.py" \
  "${root}/tests/fixtures/globalopt_witness_contract_fixture.py" \
  "${root}/tests/fixtures/predicate_provenance_verifier_fixture.py" \
  "${root}/tests/fixtures/campaign_globalopt_witness_fixture.py" \
  "${root}/tests/fixtures/workflow_globalopt_fixture.py" \
  "${root}/tests/fixtures/transaction_evidence_fixture.py"

echo "registry contract validation"
CHECK_REGISTRIES_OUT="${out_dir}/registry-contract" \
PYTHON3="${python_bin}" \
Z3="${z3_bin}" \
"${root}/scripts/check-registries.sh"

echo "focused GlobalOpt coverage"
"${python_bin}" "${root}/tests/fixtures/globalopt_coverage_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/coverage" \
  --z3 "${z3_bin}"

echo "GlobalOpt witness contract verification"
"${python_bin}" "${root}/tests/fixtures/globalopt_witness_contract_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/witness-contract" \
  --z3 "${z3_bin}"

echo "predicate provenance verification"
"${python_bin}" "${root}/tests/fixtures/predicate_provenance_verifier_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/predicate-provenance"

echo "campaign GlobalOpt witness propagation"
"${python_bin}" "${root}/tests/fixtures/campaign_globalopt_witness_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/campaign"

echo "workflow GlobalOpt feed-through"
"${python_bin}" "${root}/tests/fixtures/workflow_globalopt_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/workflow" \
  --z3 "${z3_bin}"

echo "transaction evidence join"
"${python_bin}" "${root}/tests/fixtures/transaction_evidence_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/evidence" \
  --mode evidence

echo "transaction promotion join"
"${python_bin}" "${root}/tests/fixtures/transaction_evidence_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/promotion" \
  --mode promotion

echo "transaction audit join"
"${python_bin}" "${root}/tests/fixtures/transaction_evidence_fixture.py" \
  --repo "${root}" \
  --work-dir "${out_dir}/audit" \
  --mode audit

if [[ "${keep_output}" -eq 0 ]]; then
  clean_outputs
  echo "strict GlobalOpt check passed; output cleaned"
else
  echo "strict GlobalOpt check passed; output kept at ${out_dir}"
fi
