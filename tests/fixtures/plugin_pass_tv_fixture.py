#!/usr/bin/env python3
"""Verify a pass O2T did not build -- and refuse to certify one it never exercised.

THE GAP THIS CLOSES. O2T's strongest machinery is Track B: run the real pass and prove its
whole-function output a refinement of its input (1,826 of 1,937 real functions, cross-checked by
lli, Alive2 and Bitwuzla). It never cared whose pass produced the output. But it was unreachable
for third-party code, because every pass-runner strategy carried a `canonical_pass` and
`plan._runnable_pass` said outright: "a custom/unbuilt pass would need a build (out of scope)".
Pointed at a vendor pass those strategies fell back to validating LLVM's own InstCombine -- true
proofs about other code, which is the over-attribution this session fixed separately.

`plugin-tv` has NO canonical fallback: it runs the pass under verification through
`-load-pass-plugin`, or it does not run. A verdict from it is about the user's pass and nothing
else. O2T still builds nothing -- anyone using their pass has already built it.

THE TRAP, FOUND BY BUILDING IT. On its first end-to-end run this reported `proved` for a plugin
carrying a planted `add x,x -> x` (correct answer: `x*2`). Every number was true: three functions
really were proved to refine themselves. The pass had simply never touched them, because the
default benchmark corpus contains no `add x,x` for the bug to break. Pass-level VACUITY -- a true
statement about the wrong thing, and the one direction this project cannot afford. A run that
changes nothing is now `inconclusive` and says why.

Needs z3, opt, and a clang++ that can build against the same LLVM as `opt` (skipped otherwise).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LLVM = Path("/opt/homebrew/opt/llvm@18")
PLUGIN_SRC = Path(__file__).resolve().parent / "toy_plugin_pass.cpp"
# `sub x,0 -> x` is sound; `add x,x -> x` is the planted bug (it should be `x*2`).
CORPUS = """define i32 @sound_sub_zero(i32 %x) {
  %r = sub i32 %x, 0
  ret i32 %r
}
define i32 @planted_add_self(i32 %x) {
  %r = add i32 %x, %x
  ret i32 %r
}
"""


def main() -> int:
    opt = shutil.which("opt") or str(LLVM / "bin" / "opt")
    clangxx = str(LLVM / "bin" / "clang++")
    if (shutil.which("z3") is None or not Path(opt).exists() or not Path(clangxx).exists()
            or not (LLVM / "include" / "llvm" / "Passes" / "PassPlugin.h").exists()):
        print("plugin_pass_tv_fixture: z3/opt/clang++/LLVM headers not found, skipped")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plugin = tmp / "libToyPass.dylib"
        build = subprocess.run(
            [clangxx, "-fPIC", "-shared", "-o", str(plugin), str(PLUGIN_SRC),
             f"-I{LLVM}/include", "-std=c++17", "-fno-rtti", "-Wl,-undefined,dynamic_lookup"],
            capture_output=True, text=True, timeout=600)
        if build.returncode != 0:
            print(f"plugin_pass_tv_fixture: plugin build failed, skipped ({build.stderr[:120]})")
            return 0

        corpus = tmp / "corpus.ll"
        corpus.write_text(CORPUS)
        tool = str(ROOT / "tools" / "cv-orchestrate.py")

        def run(*extra):
            report = tmp / f"r{len(extra)}.json"
            subprocess.run([sys.executable, tool, "--source", str(PLUGIN_SRC), "--pass", "toypass",
                            "--pass-plugin", str(plugin), "--opt-bin", opt,
                            "--report", str(report), *extra],
                           capture_output=True, text=True, timeout=1800)
            entry = json.loads(report.read_text())["passes"][0]
            check = next((c for c in entry["checks"] if c["strategy"] == "plugin-tv"), None)
            assert check is not None, ("plugin-tv must be planned and run once a plugin is given",
                                       [c["strategy"] for c in entry["checks"]])
            return entry, check

        # 1) THE CATCH. Given IR the pass actually transforms, the planted miscompile is REFUTED --
        #    from a pass O2T never built, never parsed, and knows nothing about.
        entry, check = run("--pass-corpus", str(corpus))
        assert check["verdict"] == "refuted", \
            ("`add x,x -> x` must be refuted on IR that exercises it", check)
        assert check["refuted"] == 1 and check["proved"] == 1, \
            ("and the SOUND fold beside it must still prove -- a validator that refuted both would "
             "be useless", check)
        assert check["changed"] == 2, check
        assert entry["headline"]["status"] == "refuted", entry["headline"]

        # 2) THE VACUITY GUARD. With no corpus the pass runs on a default benchmark it does not
        #    touch, so every proof is a function it left alone. This reported `proved` when first
        #    built -- a true statement about the wrong thing.
        entry0, check0 = run()
        assert check0["verdict"] == "inconclusive", \
            ("a pass that changed NOTHING has not been verified, however many proofs it collects",
             check0)
        assert check0.get("changed") == 0 and "changed nothing" in check0.get("reason", ""), check0
        assert entry0["headline"]["status"] != "proved", \
            ("...and that must not reach the headline as a certification", entry0["headline"])

    print("plugin_pass_tv_fixture OK: O2T verifies a pass it did not build -- `plugin-tv` loads the "
          "user's plugin, refutes a planted `add x,x -> x` with the sound `sub x,0 -> x` still "
          "proving beside it, and REFUSES to certify when the pass transformed nothing (which it "
          "wrongly reported as `proved` the first time this ran)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
