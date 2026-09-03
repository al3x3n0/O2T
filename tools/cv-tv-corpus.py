#!/usr/bin/env python3
"""Whole-function translation validation over a corpus of `.ll` files (Track B, end-to-end).

For every function in each input `.ll`, run the REAL `opt -passes=instcombine` and prove the WHOLE
function's transformation sound (Alive2-style refinement). Reports per-status counts -- the honest
end-to-end reach-vs-decline picture over real code. See o2t/validate/corpus_tv.py.

With `--cross-check`, every function O2T PROVES is additionally confirmed by INDEPENDENT oracles that
do not share O2T's SMT encoding -- `lli` (value execution) and reference Alive2 (`alive-tv`, poison/
undef/UB) -- and any disagreement (a possible false proof) is reported and exits non-zero.
"""
import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t import toolchain  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402
from o2t.validate.corpus_tv import validate_file, cross_check_file  # noqa: E402


def _cross_check(args, z3, opt) -> int:
    """--cross-check: independently confirm every PROVED transform against lli + Alive2 (whichever are
    present) -- oracles that do NOT share O2T's SMT encoding. Any disagreement is a possible false proof
    and exits non-zero."""
    from o2t.meta.cross_check import detect_solvers
    lli = toolchain.resolve_lli(args.lli_bin)
    alive = shutil.which(args.alive_tv)
    second = [n for n, _ in detect_solvers(z3) if n != "z3"]
    if not lli and not alive and not second:
        print("cv-tv-corpus --cross-check: needs lli, alive-tv, or a second SMT solver on PATH",
              file=sys.stderr)
        return 2
    total, disagreements, vacuous, no_answer = Counter(), [], 0, []
    for path in args.ll:
        r = cross_check_file(z3, path.read_text(), opt, lli_bin=lli, alive_bin=alive, timeout=args.timeout)
        total.update(r["base"])
        vacuous += r.get("vacuous", 0)
        disagreements += [{**d, "file": path.name} for d in r["disagreements"]]
        no_answer += [{**n, "file": path.name} for n in r.get("solver_no_answer", [])]
        na = len(r.get("solver_no_answer", []))
        print(f"{path.name}: proved {r['base'].get('proved', 0)} (vacuous {r.get('vacuous', 0)}), "
              f"cross-checked {r['cross_checked']}, disagreements {len(r['disagreements'])}"
              + (f", second-solver no-answer {na}" if na else ""), flush=True)
    oracles = ", ".join([name for name, present in (("lli", lli), ("alive2", alive)) if present]
                        + second)
    print(f"CROSS-CHECK [{oracles}]: {sum(total.values())} functions, "
          f"{total.get('proved', 0)} proved ({vacuous} vacuous), {len(disagreements)} disagreement(s)")
    if args.report:
        args.report.write_text(json.dumps(
            {"counts": dict(total), "oracles": oracles, "vacuous": vacuous,
             "disagreements": disagreements, "solver_no_answer": no_answer}, indent=2) + "\n")
    if disagreements:
        print("!! INDEPENDENT ORACLE DISAGREEMENTS -- an O2T `proved` a non-encoding oracle contradicts "
              "(a possible FALSE PROOF):", file=sys.stderr)
        for d in disagreements:
            print(f"   {d}", file=sys.stderr)
        return 1
    # "Confirmed" must mean the oracles ANSWERED. A second solver that timed out has confirmed
    # nothing, and printing the unqualified line anyway is how a partial cross-check gets quoted as
    # a complete one -- the figure this run exists to justify.
    if no_answer:
        print(f"All proved transforms confirmed by the oracles that ANSWERED (0 disagreements), but "
              f"{len(no_answer)} had no second-solver answer (timeout/error) and are NOT "
              f"independently confirmed at the solver layer:")
        for n in no_answer[:20]:
            print(f"   {n['file']}:{n['function']} -- no answer from {', '.join(n['solvers']) or 'second solver'}")
        if len(no_answer) > 20:
            print(f"   ... and {len(no_answer) - 20} more (see --report)")
        return 0
    print("All proved transforms independently confirmed (0 disagreements).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ll", type=Path, nargs="+", help="LLVM .ll file(s)")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--timeout", type=int, default=15,
                    help="per-function WALL-CLOCK backstop (s); rlimit is what normally decides")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="validate this many functions in parallel. Safe ONLY because the solver "
                         "budget is deterministic (see --rlimit): with a wall-clock budget, "
                         "contention alone flips proved into timeout. 0 uses every core.")
    ap.add_argument("--rlimit", type=int, default=None,
                    help="per-query DETERMINISTIC solver budget (z3 rlimit units). Unlike a "
                         "wall-clock timeout this gives the same verdict on a busy machine as on "
                         "an idle one, so a sweep is reproducible and may run in parallel. "
                         "0 disables it and restores pure wall-clock behaviour.")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--show", choices=["refuted", "unsupported", "timeout", "error", "all"],
                    help="also list function names in this status")
    ap.add_argument("--cross-check", action="store_true",
                    help="independently confirm every PROVED transform with lli + Alive2 (if present)")
    ap.add_argument("--lli-bin", default="lli")
    ap.add_argument("--alive-tv", default="alive-tv")
    args = ap.parse_args(argv)

    z3 = shutil.which(args.z3_bin)
    opt = tv._resolve_opt(args.opt_bin)
    if z3 is None or opt is None:
        print("cv-tv-corpus: z3 and opt(18) required", file=sys.stderr)
        return 2
    if args.cross_check:
        return _cross_check(args, z3, opt)
    total = Counter()
    files, vacuous, skipped = [], 0, []
    for path in args.ll:
        jobs = (os.cpu_count() or 1) if args.jobs == 0 else args.jobs
        r = validate_file(z3, path.read_text(), opt, timeout=args.timeout, rlimit=args.rlimit,
                          jobs=jobs)
        # A WHOLE-FILE `opt` FAILURE MUST NOT READ AS "NOTHING TO DO". `validate_file` records it
        # as `opt_ok: False` and returns no functions, and this loop used to print the resulting
        # empty count dict and move on -- so a file whose every function vanished looked exactly
        # like a file with no work in it, and silently left the aggregate's DENOMINATOR. That is
        # how `shift.ll`'s 171 functions sat outside a corpus figure reported as nine files.
        if not r.get("opt_ok", True):
            skipped.append(path.name)
            print(f"{path.name}: !! OPT FAILED -- 0 of this file's functions were validated; it is "
                  f"NOT in the aggregate below", file=sys.stderr)
            continue
        total.update(r["counts"])
        vacuous += r.get("vacuous", 0)
        files.append({"file": str(path), "counts": r["counts"], "vacuous": r.get("vacuous", 0),
                      "functions": r["functions"] if args.show else None})
        listed = [f["function"] for f in r["functions"]
                  if args.show and (args.show == "all" or f["status"] == args.show)]
        print(f"{path.name}: {dict(r['counts'])}"
              + (f" vacuous={r['vacuous']}" if r.get("vacuous") else "")
              + (f"  {listed}" if listed else ""))
    n = sum(total.values())
    proved = total.get("proved", 0)
    summary = {"functions": n, "counts": dict(total), "vacuous": vacuous,
               "proved_pct": (100 * proved // n) if n else 0, "refuted": total.get("refuted", 0),
               "opt_failed_files": skipped}
    print(f"AGGREGATE: proved {proved}/{n} ({summary['proved_pct']}%), refuted {summary['refuted']}, "
          f"vacuous {vacuous} (proved only because the source is UB/poison everywhere)")
    if skipped:
        print(f"!! {len(skipped)} FILE(S) NOT MEASURED AT ALL (opt failed): {', '.join(skipped)} -- "
              f"the percentage above is over the REMAINING files", file=sys.stderr)
    if args.report:
        args.report.write_text(json.dumps({"summary": summary, "files": files}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
