"""Shared explicit configuration for the pinned RV example workflow."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys


def context():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    args, rest = parser.parse_known_args()
    sys.argv = [sys.argv[0], *rest]
    config = json.loads(args.config.read_text())
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    return SimpleNamespace(root=root, out=args.out_dir.resolve(), config=args.config.resolve(),
                           **{k: v if k in ('revision', 'cases') else Path(v) for k, v in config.items()})
