#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: fake-alive-tv-unsupported.sh BEFORE AFTER" >&2
  exit 2
fi

echo "Unsupported instruction for this fixture"
echo ""
echo "Summary:"
echo "  0 correct transformations"
echo "  0 incorrect transformations"
echo "  1 failed-to-prove transformations"
echo "  0 Alive2 errors"
exit 1
