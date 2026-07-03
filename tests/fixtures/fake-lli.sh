#!/usr/bin/env bash
# Deterministic stand-in for lli: exit code = content checksum mod 256, so two
# modules with identical content "execute" the same and different content differs.
set -uo pipefail
f=${1:-}
[[ -s "$f" ]] || exit 200
exit $(cksum "$f" | awk '{print $1 % 256}')
