#!/usr/bin/env bash
set -euo pipefail

image=${O2T_KLEE_IMAGE:-${COMPILERVERIF_KLEE_IMAGE:-klee/klee:3.0}}
platform=${O2T_DOCKER_PLATFORM:-${COMPILERVERIF_DOCKER_PLATFORM:-linux/amd64}}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
workspace_root=$(cd "${script_dir}/../.." && pwd)

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon unavailable. Start Docker and retry." >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  set -- bash
fi

docker_args=(
  --rm \
  --platform "${platform}" \
  --ulimit stack=-1:-1 \
  -v "${workspace_root}:/work" \
  -w /work/O2T \
)

if [[ -n "${O2T_KLEE_CXX:-${COMPILERVERIF_KLEE_CXX:-}}" ]]; then
  export O2T_KLEE_CXX="${O2T_KLEE_CXX:-${COMPILERVERIF_KLEE_CXX:-}}"
  docker_args+=(-e O2T_KLEE_CXX)
fi

exec docker run "${docker_args[@]}" "${image}" "$@"
