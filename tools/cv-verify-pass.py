#!/usr/bin/env python3
"""verify-pass: single-entry autonomous LLVM pass verification + self-improve loop.

Ties the whole pipeline into one command:

    MINE (or load findings) -> LIFT whole transform from raw strings (cv-lift-finding)
      -> PROVE (z3) -> CROSS-VALIDATE vs real opt (cv-cross-validate, mini-Alive2)
      -> aggregate per-pass DOSSIER -> TRIAGE refutations (cv-triage)

and emits a unified report. Every "verified" rests on proof AND real-opt TV.

Self-improve loop (--loop): each round accumulates the newly-VERIFIED transforms
into a persistent catalog and synthesizes a born-proven declarative lift rule for
each (the reverse of cv_lift_rules.instantiate). It converges (fixpoint) when a
round discovers no NEW verified transform -- the autonomous catalog has stopped
growing.

  --findings FILE / --mine SNIPPET / --selftest (committed mixed findings)
  --loop          run the self-improve loop to convergence
  --promote FILE  write the synthesized candidate lift rules here
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LIFT = HERE / "cv-lift-finding.py"
CROSS = HERE / "cv-cross-validate.py"
TRIAGE = HERE / "cv-triage.py"
DEFAULT_FINDINGS = ROOT / "tests" / "fixtures" / "autoverify_findings.jsonl"

BV_TO_BINOP = {"bvadd": "add", "bvsub": "sub", "bvmul": "mul", "bvand": "and",
               "bvor": "or", "bvxor": "xor", "bvshl": "shl", "bvlshr": "lshr",
               "bvashr": "ashr"}


def pass_of(marker: str) -> str:
    parts = marker.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "probe" else "unknown"


def run_json(cmd: list[str], extra_in: dict | None = None) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        report = Path(tmp.name)
    try:
        subprocess.run(cmd + ["--report", str(report)], capture_output=True, text=True)
        return json.loads(report.read_text()) if report.stat().st_size else {}
    except (OSError, json.JSONDecodeError):
        return {}
    finally:
        report.unlink(missing_ok=True)


def write_temp(obj) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(obj, tmp)
        return tmp.name


# --------------------------------------------------------------------------- #

def final_status(lift_status: str, cross_verdict: str | None) -> str:
    if lift_status == "refuted":
        return "suspected-bug"
    if lift_status != "proved":
        return "unlifted"
    if cross_verdict == "verified":
        return "verified"
    if cross_verdict == "bug":
        return "suspected-bug"
    if cross_verdict == "no-trigger":
        return "conditional"
    return "proved-only"


def run_pipeline(source_args: list[str], z3_bin: str) -> dict:
    lift = run_json([sys.executable, str(LIFT), *source_args, "--z3-bin", z3_bin])
    if lift.get("status") == "skipped":
        return {"status": "skipped", "reason": lift.get("reason")}
    results = lift.get("results", [])
    cross = {}
    if results:
        lp = write_temp(lift)
        cross = run_json([sys.executable, str(CROSS), "--transforms", lp, "--z3-bin", z3_bin])
        Path(lp).unlink(missing_ok=True)
    cross_by_marker: dict[str, list] = {}
    for r in cross.get("results", []):
        cross_by_marker.setdefault(r["marker"], []).append(r.get("verdict"))

    transforms, triage_inputs = [], []
    for r in results:
        marker = r["marker"]
        verdicts = cross_by_marker.get(marker, [])
        cv = verdicts.pop(0) if verdicts else None
        fs = final_status(r.get("status"), cv)
        t = {"marker": marker, "pass": pass_of(marker), "status": fs,
             "faithful": r.get("faithful"), "guards": r.get("guards", []),
             "before": r["before"], "after": r["after"], "variables": r["variables"]}
        transforms.append(t)
        if fs == "suspected-bug":
            triage_inputs.append({"marker": marker, "faithful": r.get("faithful"),
                                  "before": r["before"], "after": r["after"],
                                  "variables": r["variables"],
                                  "tv": "refuted" if cv == "bug" else None})
    triage = {}
    if triage_inputs:
        tp = write_temp(triage_inputs)
        triage = run_json([sys.executable, str(TRIAGE), "--transforms", tp, "--z3-bin", z3_bin])
        Path(tp).unlink(missing_ok=True)
        verdict_by = {r["marker"]: r for r in triage.get("results", [])}
        for t in transforms:
            if t["status"] == "suspected-bug" and t["marker"] in verdict_by:
                t["triage"] = verdict_by[t["marker"]]["verdict"]
                t["triage_reason"] = verdict_by[t["marker"]].get("reason", "")

    verified = [t for t in transforms if t["status"] == "verified"]
    total = len(transforms) + lift.get("counts", {}).get("skip", 0) + lift.get("counts", {}).get("unliftable", 0)
    return {"transforms": transforms, "verified": verified,
            "coverage_pct": round(100.0 * len(verified) / total, 1) if total else 0.0,
            "totals": {"mined_sites": total, "verified": len(verified),
                       "suspected_bugs": sum(1 for t in transforms if t["status"] == "suspected-bug")}}


# --------------------------------------------------------------------------- #
# self-improve: verified transform -> declarative lift rule (reverse instantiate)
# --------------------------------------------------------------------------- #

def dsl_to_template(node):
    op = node["op"]
    if op == "var":
        return {"var": node["name"]}
    if op == "bvconst":
        return {"const": int(node["value"])}
    if op in BV_TO_BINOP:
        return {"binop": BV_TO_BINOP[op], "args": [dsl_to_template(a) for a in node["args"]]}
    if op == "bvneg":
        return {"unop": "neg", "args": [dsl_to_template(node["args"][0])]}
    raise ValueError(f"cannot templatize {op}")


def synthesize_rule(t: dict) -> dict | None:
    try:
        before, after = dsl_to_template(t["before"]), dsl_to_template(t["after"])
    except ValueError:
        return None
    root = t["before"].get("op")
    return {"name": t["marker"].split(".")[-1], "match": {"marker": t["marker"]},
            "operation": BV_TO_BINOP.get(root, root), "variables": t["variables"],
            "before": before, "after": after, "provenance": "verified: proof + real-opt TV"}


def transform_key(t: dict) -> str:
    return t["marker"] + "|" + json.dumps(t["before"], sort_keys=True) + "|" + json.dumps(t["after"], sort_keys=True)


# --------------------------------------------------------------------------- #

def render(report: dict) -> str:
    t = report["totals"]
    lines = ["verify-pass report", "=" * 30,
             f"mined: {t['mined_sites']}  verified: {t['verified']}  "
             f"coverage: {report['coverage_pct']}%  suspected-bugs: {t['suspected_bugs']}", ""]
    for tr in report["transforms"]:
        extra = ""
        if tr["status"] == "suspected-bug":
            extra = f"  triage={tr.get('triage', '?')}"
        faith = "" if tr.get("faithful") in (None, True) else " [UNFAITHFUL]"
        lines.append(f"  [{tr['status']:13}] {tr['marker']}{faith}{extra}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--findings", type=Path)
    src.add_argument("--mine", type=Path, metavar="SNIPPET")
    src.add_argument("--selftest", action="store_true")
    ap.add_argument("--loop", action="store_true", help="run the self-improve loop to convergence")
    ap.add_argument("--promote", type=Path, help="write synthesized candidate lift rules here")
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    z3_bin = shutil.which(args.z3_bin)
    if z3_bin is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    source_args = (["--mine", str(args.mine)] if args.mine is not None
                   else ["--findings", str(args.findings or DEFAULT_FINDINGS)])

    if not args.loop:
        report = run_pipeline(source_args, z3_bin)
        if report.get("status") == "skipped":
            print(json.dumps(report))
            return 0
        if args.promote:
            rules = [r for r in (synthesize_rule(t) for t in report["verified"]) if r]
            args.promote.write_text(json.dumps({"model": "lift-rules-v1", "rules": rules},
                                               indent=2, sort_keys=True) + "\n")
        if args.report:
            args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(render(report))
        return 0 if report["totals"]["verified"] > 0 else 1

    # self-improve loop: accumulate verified catalog until a round adds nothing new
    catalog: dict[str, dict] = {}
    rounds = []
    converged = False
    for _ in range(args.max_rounds):
        report = run_pipeline(source_args, z3_bin)
        if report.get("status") == "skipped":
            print(json.dumps(report))
            return 0
        new = [t for t in report["verified"] if transform_key(t) not in catalog]
        for t in new:
            catalog[transform_key(t)] = t
        rounds.append({"verified": report["totals"]["verified"], "new_promoted": len(new),
                       "catalog_size": len(catalog)})
        if not new:
            converged = True
            break
    loop = {"converged": converged, "rounds": rounds, "catalog_size": len(catalog),
            "rules": [r for r in (synthesize_rule(t) for t in catalog.values()) if r]}
    if args.promote:
        args.promote.write_text(json.dumps({"model": "lift-rules-v1", "rules": loop["rules"]},
                                           indent=2, sort_keys=True) + "\n")
    if args.report:
        args.report.write_text(json.dumps(loop, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"converged": converged, "rounds": rounds,
                      "promoted_rules": len(loop["rules"])}, sort_keys=True))
    for i, r in enumerate(rounds, 1):
        print(f"  round {i}: verified={r['verified']} new_promoted={r['new_promoted']} "
              f"catalog={r['catalog_size']}", file=sys.stderr)
    print(f"  -> {'CONVERGED' if converged else 'max-rounds'} with {len(loop['rules'])} born-proven rules",
          file=sys.stderr)
    return 0 if converged else 1


if __name__ == "__main__":
    sys.exit(main())
