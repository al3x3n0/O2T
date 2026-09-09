#!/usr/bin/env python3
"""Design a verification campaign from source and supported O2T capabilities."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from o2t.agent.campaign_design import main
if __name__ == '__main__':
    raise SystemExit(main())
