#!/usr/bin/env python3
"""Moved to o2t.frontend.scev_loop; this shim preserves the old path."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.frontend.scev_loop import *  # noqa: F401,F403,E402
