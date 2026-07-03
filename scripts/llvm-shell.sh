#!/usr/bin/env bash
set -euo pipefail

image=${O2T_LLVM_IMAGE:-${COMPILERVERIF_LLVM_IMAGE:-silkeh/clang:18}}
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

exec docker run \
  --rm \
  --platform "${platform}" \
  -u "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -v "${workspace_root}:/work" \
  -w /work/O2T \
  "${image}" \
  "$@"
