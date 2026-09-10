#!/usr/bin/env python3
"""Author a campaign from repository evidence; optional legacy RV selection mode."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == '__main__':
    selector=argparse.ArgumentParser(add_help=False)
    selector.add_argument('--adapter',choices=['autonomous','rv'],default='autonomous')
    mode,rest=selector.parse_known_args()
    if mode.adapter=='rv':
        from o2t.agent.campaign_design import main
    else:
        from o2t.agent.campaign_planner import main
    raise SystemExit(main(rest))
