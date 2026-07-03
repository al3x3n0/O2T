#!/usr/bin/env python3
"""Moved to o2t.registry.optimization_registry; re-export shim for legacy importers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.registry.optimization_registry import *  # noqa: F401,F403,E402
