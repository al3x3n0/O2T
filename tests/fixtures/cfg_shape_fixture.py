#!/usr/bin/env python3
"""Cover the SimplifyCFG diamond->select if-conversion contract (cfg_shape.py).

Asserts the parser recovers the diamond's merge-phi semantics and the optimized select, the
discharge PROVES a correct if-conversion equivalent for all inputs, and a corrupted conversion
(swapped operands / flipped condition without the matching swap) is REFUTED -- two-sided teeth
against a miscompiled simplifycfg. Needs z3 + opt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.validate import cfg_shape as cfg


def _opt():
    return shutil.which("opt") or (lambda p: str(p) if p.exists() else None)(
        Path("/opt/homebrew/opt/llvm@18/bin/opt"))


def main() -> int:
    z3, opt = shutil.which("z3"), _opt()
    if not z3 or not opt:
        print("cfg_shape_fixture: z3 or opt not found, skipped")
        return 0

    src = (ROOT / "tests" / "fixtures" / "cfg_diamond.ll").read_text()
    opt_text = cfg.run_simplifycfg(src, opt)
    assert opt_text and "select" in opt_text, "simplifycfg should produce a select"

    # 1) parser: the diamond's merge value, by branch label.
    d = cfg.parse_diamond(src, "diamond")
    assert d == {"cond": "%c", "then": "%a", "else": "%b"}, d
    s = cfg.parse_select(opt_text, "diamond")
    assert s and s["cond"] == "%c" and not s["negated"], s

    # 2) discharge: a correct if-conversion PROVES for both diamonds.
    for fn in ("diamond", "diamondSwapped"):
        v = cfg.validate_simplifycfg(z3, opt_text, src, fn)
        assert v["status"] == "proved", (fn, v)

    # 3) TEETH: corrupt the optimized select -> must be REFUTED with a witness.
    swapped = opt_text.replace("select i1 %c, i32 %a, i32 %b", "select i1 %c, i32 %b, i32 %a")
    rs = cfg.validate_simplifycfg(z3, swapped, src, "diamond")
    assert rs["status"] == "refuted" and rs.get("model"), ("swapped not refuted", rs)
    flipped = opt_text.replace("select i1 %c, i32 %a, i32 %b",
                               "%nc = xor i1 %c, true\n  %a.b = select i1 %nc, i32 %a, i32 %b")
    rf = cfg.validate_simplifycfg(z3, flipped, src, "diamond")
    assert rf["status"] == "refuted", ("flipped condition not refuted", rf)

    # 4) the CLI tool: baseline proves all, --mutate refutes at least one.
    tool = ROOT / "tools" / "cv-validate-cfg.py"
    base = subprocess.run([sys.executable, str(tool), "--opt-bin", opt], capture_output=True, text=True)
    assert base.returncode == 0, base.stderr
    mut = subprocess.run([sys.executable, str(tool), "--opt-bin", opt, "--mutate"],
                         capture_output=True, text=True)
    assert mut.returncode == 0 and '"refuted": 1' in mut.stdout, mut.stdout

    print("cfg_shape_fixture OK: diamond→select if-conversion proved for all inputs; "
          "swapped/flipped conversions refuted with witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
