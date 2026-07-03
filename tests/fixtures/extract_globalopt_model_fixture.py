#!/usr/bin/env python3
"""Cover source-recovered GlobalOpt verification (intent/extract_globalopt_model.py).

Asserts the miner recovers each initializer-defaulting fold's auditable legality (internal
linkage / no observing use), proves a fold guarded by `hasLocalLinkage() && use_empty()`, and
REFUTES one that defaults the initializer guarded only by the opaque `isGlobalInitializerDead`
(no linkage/use guard) -- catching an unsound GlobalOpt-like pass from its source. Needs z3."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import extract_globalopt_model as eg

FX = ROOT / "tests" / "fixtures"


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("extract_globalopt_model_fixture: z3 not found, skipped")
        return 0

    # 1) the sound fold (hasLocalLinkage + use_empty) is recovered and PROVED.
    sound = {r["function"]: r for r in eg.verify_source(
        z3, (FX / "global_dead_initializer_snippet.cpp").read_text())}
    s = sound["removeDeadGlobalInitializer"]
    assert s["local_linkage"] and s["use_empty"] and s["status"] == "proved", s

    # 2) the unsafe fold (guarded only by isGlobalInitializerDead) is REFUTED with a witness.
    unsafe = {r["function"]: r for r in eg.verify_source(
        z3, (FX / "global_dead_initializer_unsafe_snippet.cpp").read_text())}
    u = unsafe["removeUnsafeGlobalInitializer"]
    assert not u["local_linkage"] and not u["use_empty"], u
    assert u["status"] == "refuted" and u.get("witness"), ("unsafe fold not caught", u)

    # 3) a vendor-namespaced GlobalOpt-like pass with the full guard also proves.
    vendor = {r["function"]: r for r in eg.verify_source(
        z3, (FX / "third_party_globalopt_like_pass.cpp").read_text())}
    assert vendor["stripDormantInitializer"]["status"] == "proved", vendor

    # 4) recognition helper directly: a non-defaulting fold is not a transform; missing the use
    #    guard alone flips the legality (internal but possibly loaded -> read-before-store).
    assert eg.recognize_initializer_default("void f(){ GV->eraseFromParent(); }") is None
    m = eg.recognize_initializer_default(
        "void f(){ if (GV->hasLocalLinkage()) GV->setInitializer(getNullValue(T)); }")
    assert m and m["local_linkage"] and not m["use_empty"], m

    # 5) the CLIs agree.
    mine = ROOT / "tools" / "cv-mine-globalopt-pass.py"
    p1 = subprocess.run([sys.executable, str(mine)], capture_output=True, text=True)
    assert p1.returncode == 0 and '"proved": 1' in p1.stdout, p1.stdout
    p2 = subprocess.run([sys.executable, str(mine), "--source",
                         str(FX / "global_dead_initializer_unsafe_snippet.cpp")],
                        capture_output=True, text=True)
    assert p2.returncode == 0 and '"refuted": 1' in p2.stdout, p2.stdout

    print("extract_globalopt_model_fixture OK: dead-initializer folds recovered from source and "
          "discharged; a fold that defaults an initializer without a linkage/use guard refuted "
          "with a witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
