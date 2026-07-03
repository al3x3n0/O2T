#!/usr/bin/env python3
"""Closed-loop TRANSLATION VALIDATION: prove a REAL LLVM pass's output ≡ its input.

Everything else in O2T proves HAND-WRITTEN before/after pairs. This runs the actual pass --
`opt -passes=<X>` on a source function -- then proves opt's *real output* equivalent to the
input for all trip counts. It composes the whole stack: the SCEV frontend extracts the loop
recurrences from both the original and the optimized IR, and the relational prover
(cv-mine-relational.prove_mined) discharges the simulation relation. The prover is unchanged;
this is the integration that turns O2T from "validate my model" into a MISCOMPILE FINDER.

Two transform shapes:
  * loop -> loop  (loop-reduce/LSR, licm, loop-rotate, simple-loop-unswitch, loop-instsimplify):
    both sides still have a loop recurrence -> prove_mined proves the outputs equal.  PROVED.
  * loop -> closed form  (indvars/scalar-evolution deletes the accumulator, returning a closed
    form in the exit block): the optimized side has no loop recurrence.  Reported honestly as
    `loop-eliminated` -- a real transform this prover does not yet validate (closed-form mode is
    future work), NEVER silently passed.

Teeth: `--mutate` perturbs one phi initial value in opt's output (`[ 0, %entry ]` -> `[ 1, .. ]`),
simulating a transform that miscompiled the recurrence; the validator must then REFUSE it. That
is the proof that a real miscompile would be caught, not rubber-stamped.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from o2t.frontend import scev_loop as scev
from o2t.mine import relational as minerel
from o2t.validate.witness import find_witness
from o2t.validate.differential import differential
from o2t.validate.closed_form import validate_closed_form

_DEF_RE = re.compile(r"define\b[^@]*@(\w+)\s*\(([^)]*)\)")


def run_pass(ll_text, passes, opt_bin="opt"):
    """Run `opt -passes=<passes>` and return the optimized IR text, or None on failure."""
    opt = scev.find_opt(opt_bin)
    if opt is None:
        return None
    proc = subprocess.run([opt, "-passes=" + passes, "-S", "-o", "-"],
                          input=ll_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 and proc.stdout.strip() else None


def consts_of(ll_text, fn):
    m = re.search(r"define\b[^@]*@" + re.escape(fn) + r"\s*\(([^)]*)\)", ll_text)
    return [scev.sanitize(p.split()[-1]) for p in m.group(1).split(",") if p.strip()] if m else []


def function_names(ll_text):
    return [m.group(1) for m in _DEF_RE.finditer(ll_text)]


def mutate_source(src_text):
    """Simulate a pass that miscompiles a recurrence STEP: change each accumulator's per-iteration
    added value from %c to %n (a different param), leaving the base at 0. Optimizing this and
    comparing to the TRUE source must yield output-not-preserved -- the proof the validator would
    catch a real miscompile rather than rubber-stamp it."""
    return (src_text
            .replace("%acc.next = add i32 %acc, %c", "%acc.next = add i32 %acc, %n")  # sumConst: +c -> +n
            .replace("%t = mul i32 %i, %c", "%t = mul i32 %i, %n")                    # sumProduct: i*c -> i*n
            .replace("%d = shl i32 %i, 1", "%d = shl i32 %i, 2")                      # shiftLeft: i<<1 -> i<<2
            .replace("%sq = mul i32 %i, %i", "%sq = mul i32 %i, %n")                  # sumSquares: i*i -> i*n (cubic->affine)
            .replace("%i3 = mul i32 %i2, %i", "%i3 = mul i32 %i2, %n"))               # sumCubes: i^3 -> i^2*n (quartic->cubic)


def validate_function(src_text, opt_text, fn, z3_bin, opt_bin="opt", clang_bin=None):
    """Verdict for one function: proved | output-not-preserved | loop-eliminated | no-source-loop.
    When the loop is eliminated (closed form) and `clang_bin` is given, attach a SEMI-FORMAL
    differential verdict (compile + run both on an input sweep)."""
    after = scev.scev_loop_tuple(opt_text, fn, opt_bin)
    if after is None:
        # opt replaced the loop accumulation with a closed form (e.g. indvars). FORMAL tier
        # first: for a recognized counted loop, prove the closed form equals the source exit
        # value (closed_form.validate_closed_form -- it has its OWN source recognizer, so this
        # works even for cubic sources `scev_loop_tuple` cannot extract). Only when it declines
        # do we fall back to the SEMI-FORMAL differential -- never silently passed either way.
        ops = scev.closed_form_boundary_ops(opt_text, fn, opt_bin)
        cf = validate_closed_form(z3_bin, src_text, opt_text, fn, opt_bin)
        if cf["status"] == "proved":
            return {"function": fn, "status": "proved-closed-form",
                    "bound": cf.get("bound"), "closed_form_ops": ops, "widenings": cf.get("widenings")}
        if cf["status"] == "refuted":
            return {"function": fn, "status": "output-not-preserved",
                    "via": "closed-form", "witness": cf.get("counterexample")}
        out = {"function": fn, "status": "loop-eliminated", "closed_form_ops": ops,
               "closed_form_reason": cf.get("reason")}
        if clang_bin:
            out["differential"] = differential(src_text, opt_text, fn, clang_bin)
        return out
    before = scev.scev_loop_tuple(src_text, fn, opt_bin)
    if before is None:
        return {"function": fn, "status": "no-source-loop"}
    consts = consts_of(src_text, fn)
    res = minerel.prove_mined(z3_bin, minerel.build_model(before, after, consts))
    out = {"function": fn, "status": res["status"], "relation": res.get("relation")}
    if res["status"] == "output-not-preserved":
        # CEGAR: turn the abstract refutation into a concrete, minimized miscompiling input.
        out["witness"] = find_witness(before, after, consts)
    return out


def validate(src_text, passes, z3_bin, opt_bin="opt", mutate=False, clang_bin=None):
    """Run the pass over the whole module and validate every function. With `mutate`, the pass
    runs on a recurrence-corrupted source but `before` stays the TRUE source -- so a sound
    validator must REFUSE the result. With `clang_bin`, loop-eliminated cases get a semi-formal
    differential verdict."""
    opt_input = mutate_source(src_text) if mutate else src_text
    opt_text = run_pass(opt_input, passes, opt_bin)
    if opt_text is None:
        return {"passes": passes, "status": "opt-failed", "results": []}
    results = [validate_function(src_text, opt_text, fn, z3_bin, opt_bin, clang_bin)
               for fn in function_names(src_text)]
    return {"passes": passes, "mutated": mutate, "results": results}


# Real passes validated by the formal (loop->loop) tier. A pass may PROVE, or honestly report
# loop-eliminated (closed form) / no-source-loop -- but a sound pass must never be REFUTED.
SOUND_PASSES = ["loop-reduce", "licm", "loop-rotate", "simple-loop-unswitch", "loop-instsimplify",
                "gvn", "early-cse", "sccp", "reassociate", "instcombine", "mem2reg", "loop-simplify"]


def run_selftest(z3_bin, opt_bin, src_text, clang_bin=None):
    out = {"sound": [], "closed_form": None, "teeth": None, "semiformal": None,
           "semiformal_teeth": None}
    total_proved = 0
    # 1) sound passes: NO function may be refuted (output-not-preserved); loop-eliminated is a
    #    legitimate unvalidated outcome, not an unsoundness.
    for p in SOUND_PASSES:
        rep = validate(src_text, p, z3_bin, opt_bin)
        total_proved += sum(r["status"] == "proved" for r in rep["results"])
        clean = all(r["status"] != "output-not-preserved" for r in rep["results"])
        out["sound"].append({"passes": p, "clean": clean, "results": rep["results"]})
    # 2) indvars: closed-form deletion is honestly surfaced (not silently passed)
    rep = validate(src_text, "indvars", z3_bin, opt_bin, clang_bin=clang_bin)
    elim_results = [r for r in rep["results"] if r["status"] == "loop-eliminated"]
    out["closed_form"] = {"passes": "indvars", "results": rep["results"],
                          "any_loop_eliminated": bool(elim_results),
                          # FORMAL tier: loop->closed-form transforms proved equivalent over ALL n
                          # in the integers (upgrades the differential for the recognized class).
                          "formally_proved": [r["function"] for r in rep["results"]
                                              if r["status"] == "proved-closed-form"],
                          # Name the non-ring ops that still put a closed form past the integer
                          # discharge (§2) -- the precise boundary, not a blanket verdict.
                          "boundary_ops": sorted({o for r in elim_results
                                                  for o in r.get("closed_form_ops", [])})}
    # 2b) SEMI-FORMAL: differential validation closes the loop->closed-form (indvars) gap.
    if clang_bin:
        elim = [r for r in rep["results"] if r["status"] == "loop-eliminated"]
        out["semiformal"] = {"passes": "indvars",
                             "all_pass": bool(elim) and all(
                                 r.get("differential", {}).get("status") == "differential-pass" for r in elim),
                             "results": [{"function": r["function"], "differential": r.get("differential")} for r in elim]}
        # semi-formal teeth: optimizing a corrupted source must differentially MISMATCH
        rep2 = validate(src_text, "indvars", z3_bin, opt_bin, mutate=True, clang_bin=clang_bin)
        mism = [r for r in rep2["results"] if r.get("differential", {}).get("status") == "differential-mismatch"]
        out["semiformal_teeth"] = {"passes": "indvars+mutate",
                                   "caught": bool(mism) and all(r["differential"].get("witness") for r in mism),
                                   "results": [{"function": r["function"], "differential": r.get("differential")} for r in mism]}
        # FORMAL teeth: a corrupted source whose closed form is in the recognized class must be
        # REFUTED formally (output-not-preserved via closed-form) with a concrete witness n.
        cf_refuted = [r for r in rep2["results"]
                      if r["status"] == "output-not-preserved" and r.get("via") == "closed-form"]
        out["closed_form_teeth"] = {"passes": "indvars+mutate",
                                    "caught": bool(cf_refuted) and all(r.get("witness") for r in cf_refuted),
                                    "results": [{"function": r["function"], "witness": r.get("witness")} for r in cf_refuted]}
    # 3) teeth: optimizing a recurrence-corrupted source must be REFUSED against the true source
    rep = validate(src_text, "licm", z3_bin, opt_bin, mutate=True)
    refuted = [r for r in rep["results"] if r["status"] == "output-not-preserved"]
    # CEGAR: every refutation must carry a concrete witness that actually diverges. (Functions the
    # mutation does not touch may legitimately still prove -- the signal is a refutation+witness.)
    witnessed = [r for r in refuted if r.get("witness")
                 and r["witness"]["source"] != r["witness"]["optimized"]]
    out["teeth"] = {"passes": "licm+mutate", "results": rep["results"],
                    "caught": bool(refuted),
                    "witnessed": len(witnessed) == len(refuted) and bool(refuted)}
    semiformal_ok = (clang_bin is None
                     or (out["semiformal"]["all_pass"] and out["semiformal_teeth"]["caught"]
                         and out["closed_form_teeth"]["caught"]))
    out["ok"] = (all(s["clean"] for s in out["sound"]) and total_proved >= 3
                 and out["closed_form"]["any_loop_eliminated"]
                 # FORMAL closed-form tier proves at least one indvars loop->closed-form outright.
                 and bool(out["closed_form"]["formally_proved"])
                 and out["teeth"]["caught"] and out["teeth"]["witnessed"]
                 and semiformal_ok)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--source", type=Path,
                    default=Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "translation_validation.ll")
    ap.add_argument("--passes", help="run a specific pass pipeline and validate (non-selftest)")
    ap.add_argument("--mutate", action="store_true", help="inject a recurrence miscompile (teeth)")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--clang-bin", default="clang", help="enables semi-formal differential validation")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    import shutil
    from o2t.validate.differential import find_clang
    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0
    if scev.find_opt(args.opt_bin) is None:
        print(json.dumps({"status": "skipped", "reason": "opt (LLVM) not found"}))
        return 0
    clang_bin = find_clang(args.clang_bin)  # None disables the semi-formal layer

    src_text = args.source.read_text()
    if args.selftest:
        report = run_selftest(z3_bin, args.opt_bin, src_text, clang_bin)
    else:
        report = validate(src_text, args.passes or "loop-reduce", z3_bin, args.opt_bin,
                          args.mutate, clang_bin)
        report["ok"] = all(r["status"] in ("proved", "no-source-loop", "loop-eliminated")
                           and r.get("differential", {}).get("status") not in ("differential-mismatch",)
                           for r in report["results"])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"ok": report.get("ok"), "selftest": args.selftest}, sort_keys=True))
    if args.selftest:
        for s in report["sound"]:
            verds = ",".join(f"{r['function']}={r['status']}" for r in s["results"])
            print(f"  [{'ok' if s['clean'] else 'FAIL'}] sound pass {s['passes']}: {verds}", file=sys.stderr)
        print(f"  [{'ok' if report['closed_form']['any_loop_eliminated'] else 'FAIL'}] "
              f"indvars -> loop-eliminated (honestly surfaced)", file=sys.stderr)
        print(f"  [{'ok' if report['teeth']['caught'] else 'FAIL'}] mutated output REFUSED (teeth)",
              file=sys.stderr)
        for r in report["teeth"]["results"]:
            if r.get("witness"):
                w = r["witness"]
                print(f"       witness {r['function']}: params={w['params']} trip={w['trip_count']} "
                      f"=> source={w['source']} vs optimized={w['optimized']}", file=sys.stderr)
        print(f"  [{'ok' if report['teeth']['witnessed'] else 'FAIL'}] every refutation carries a "
              f"concrete witness", file=sys.stderr)
        if report.get("semiformal"):
            sf = report["semiformal"]
            print(f"  [{'ok' if sf['all_pass'] else 'FAIL'}] SEMI-FORMAL: indvars (loop->closed-form) "
                  f"differential-pass on {[r['function'] for r in sf['results']]}", file=sys.stderr)
            st = report["semiformal_teeth"]
            for r in st.get("results", []):
                w = r["differential"]["witness"]
                print(f"       diff-witness {r['function']}: params={w['params']} "
                      f"=> source={w['source']} vs optimized={w['optimized']}", file=sys.stderr)
            print(f"  [{'ok' if st['caught'] else 'FAIL'}] SEMI-FORMAL teeth: corrupted indvars "
                  f"output differentially REFUSED", file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
