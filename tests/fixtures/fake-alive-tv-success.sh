#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: fake-alive-tv-success.sh BEFORE AFTER" >&2
  exit 2
fi

echo "Transformation seems to be correct!"
echo ""
echo "Summary:"
echo "  1 correct transformations"
echo "  0 incorrect transformations"
echo "  0 failed-to-prove transformations"
echo "  0 Alive2 errors"
