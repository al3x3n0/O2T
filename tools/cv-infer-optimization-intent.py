#!/usr/bin/env python3
"""CLI + re-export shim -> o2t.intent.infer (logic lives in the package).

Re-exports all public symbols, not just main(): some fixtures SourceFileLoader this
file and call internals (e.g. lower_guard_effects), so the shim must be transparent.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.intent.infer import *  # noqa: F401,F403,E402
from o2t.intent.infer import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
