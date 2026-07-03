#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: fake-alive-tv-fail.sh BEFORE AFTER" >&2
  exit 2
fi

echo "ERROR: Mismatch in memory"
echo ""
echo "Summary:"
echo "  0 correct transformations"
echo "  1 incorrect transformations"
echo "  0 failed-to-prove transformations"
echo "  0 Alive2 errors"
exit 1
