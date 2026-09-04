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

        # 1b) VECTOR IR, which the scalar translator cannot read at all. `plugin-tv` first shipped
        #     calling `scalar_ir.validate_transform` directly instead of Track B's dispatcher, so a
        #     vector or memory pass -- exactly what someone reaches for `--pass-plugin` to check --
        #     came back `unsupported` on every function and the planted bug was MISSED. Measured on
        #     this very corpus: scalar-only said "non-integer type <4 x i32>" twice; the dispatcher
        #     proves the sound fold and refutes the planted one. The fallbacks (theory-of-arrays
        #     memory, vector lane model) are each sound in scope and decline out of it, so this
        #     widens reach without widening what is claimed.
        vec = tmp / "vec.ll"
        vec.write_text("""define <4 x i32> @vec_sound_sub_zero(<4 x i32> %x) {
  %r = sub <4 x i32> %x, zeroinitializer
  ret <4 x i32> %r
}
define <4 x i32> @vec_planted_add_self(<4 x i32> %x) {
  %r = add <4 x i32> %x, %x
  ret <4 x i32> %r
}
""")
        entry_v, check_v = run("--pass-corpus", str(vec))
        assert check_v["verdict"] == "refuted" and check_v["refuted"] == 1, \
            ("the planted bug must be caught on VECTOR IR too -- scalar-only reported "
             "`unsupported` here and saw nothing", check_v)
        assert check_v["proved"] == 1, ("and the sound vector fold must still prove", check_v)

        # 1c) INDEPENDENT CONFIRMATION, and the count that makes it meaningful. Every verdict above
        #     rests on ONE solver and ONE hand-written encoding; a pass O2T did not build has no
        #     corpus history to lean on, so it is where oracles that share neither matter most.
        #     `independently_confirmed` counts proofs an EXTERNAL oracle (lli execution, reference
        #     Alive2) actually examined.
        #
        #     It exists because `disagreements: 0` says the same thing whether every proof was
        #     confirmed or NO ORACLE RAN -- lli absent, alive-tv missing, plugin failed to load.
        #     That conflation was reintroduced within minutes of being fixed: the tool emitted this
        #     field on stdout under one name and in the --report file (which `_run_json` actually
        #     parses) under another, so the orchestrator read None and a run with zero oracle
        #     participation looked clean. A tool with two output paths has two contracts.
        if shutil.which("lli") or Path(LLVM / "bin" / "lli").exists():
            assert check["independently_confirmed"] is not None, \
                ("with an oracle available the count must be reported, not None -- None is how a "
                 "naming mismatch between stdout and the report file hid zero participation", check)
            assert check["independently_confirmed"] >= 1, \
                ("...and at least the one proved function must have been examined", check)
        assert check["oracle_disagreements"] == 0, \
            ("no external oracle may contradict a proof here; a disagreement is a possible FALSE "
             "PROOF and outranks the proof it contradicts", check)

        # 1d) THE CHANGE COUNT MUST IGNORE COMMENTS -- or the vacuity guard is theatre on exactly the
        #     inputs it exists to protect. `opt` strips `; CHECK-...` lines, and every real LLVM test
        #     file carries them, so comparing raw text counted comment removal as transformation:
        #     measured 207 of 207 functions "changed" on and.ll by sroa, gvn AND early-cse alike,
        #     none of which touched an instruction. A pass that does nothing would then be certified.
        #     With instructions compared instead: sroa 17, early-cse 34, instcombine 171.
        #
        #     THE FIXTURE PASSED THROUGHOUT, because the toy corpus above has no comments. A test
        #     written alongside the code inherits the code's blind spot; this case exists because
        #     real IR, not a fixture, exposed it.
        commented = tmp / "commented.ll"
        commented.write_text("""define i32 @untouched(i32 %x) {
; CHECK-LABEL: @untouched(
; CHECK-NEXT:    ret i32 [[X:%.*]]
;
  ret i32 %x
}
""")
        _, check_c = run("--pass-corpus", str(commented))
        assert check_c.get("changed") == 0, \
            ("stripping CHECK comments is not a transformation -- counting it as one makes the "
             "vacuity guard unable to fire on any real LLVM test file", check_c)
        assert check_c["verdict"] == "inconclusive", \
            ("...and a pass that touched nothing must decline to certify", check_c)

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
          "proving beside it -- on SCALAR and on VECTOR IR, the latter invisible to the scalar "
          "translator that this first shipped with -- and REFUSES to certify when the pass "
          "transformed nothing (which it wrongly reported as `proved` the first time this ran). "
          "Proofs are confirmed by oracles that do not share O2T's encoding, and the count of "
          "proofs they actually EXAMINED is asserted -- `disagreements: 0` alone cannot tell "
          "confirmation from an oracle that never ran")
    return 0


if __name__ == "__main__":
    sys.exit(main())
