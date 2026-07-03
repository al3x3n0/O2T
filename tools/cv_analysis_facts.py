#!/usr/bin/env python3
"""Moved to o2t.facts.analysis_facts; re-export shim for legacy importers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.facts.analysis_facts import *  # noqa: F401,F403,E402
