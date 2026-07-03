#!/usr/bin/env python3
"""CLI shim -> o2t.validate.translation (logic lives in the package)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.validate.translation import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
