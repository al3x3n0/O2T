#!/usr/bin/env python3
"""Failing O2T LLM-command fixture."""

from __future__ import annotations

import sys


def main() -> int:
    sys.stdin.read()
    print("synthetic model failure", file=sys.stderr)
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
