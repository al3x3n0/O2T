#!/usr/bin/env python3
"""Per-pass verification dossier (autonomous-verify #3).

Ties the autonomous pipeline together and reports, per LLVM pass, what was
verified end-to-end. Orchestrates:

    cv-lift-finding   (mine/lift + z3 prove + faithfulness)
    cv-cross-validate (real opt + mini-Alive2 translation validation)

and merges them by marker into a dossier. The headline status is the CONJUNCTION
(trust model): a transform is VERIFIED only when the deductive proof passed AND
the real-opt TV confirmed it.

  per transform:
    verified      proof passed AND opt fired the transform AND TV proved
    conditional   proved, but opt did not fire it here (coverage gap / no trigger)
    proved-only   proved; cross-validation unavailable (no opt)
    suspected-bug proof refuted, or TV refuted the real opt -> witness
    unlifted      could not be lifted from source (reason recorded)

  coverage% = verified / (all mined sites)

Needs z3 (+ opt for the TV column). --selftest runs the committed mixed findings
(sound + one planted-unsound), so the dossier shows a realistic mix.
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
DEFAULT_FINDINGS = ROOT / "tests" / "fixtures" / "autoverify_findings.jsonl"


def pass_of(marker: str) -> str:
    parts = marker.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "probe" else "unknown"


def run_json(cmd: list[str]) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        report = Path(tmp.name)
    try:
        subprocess.run(cmd + ["--report", str(report)], capture_output=True, text=True)
        if report.exists() and report.stat().st_size:
            return json.loads(report.read_text())
        return {}
    finally:
        report.unlink(missing_ok=True)


def build_dossier(lift_report: dict, cross_report: dict) -> dict:
    lift_results = {r["marker"] + "|" + json.dumps(r.get("before"), sort_keys=True): r
                    for r in lift_report.get("results", [])}
    cross_by_marker: dict[str, list] = {}
    for r in cross_report.get("results", []):
        cross_by_marker.setdefault(r["marker"], []).append(r)

    transforms = []
    for key, lr in lift_results.items():
        marker = lr["marker"]
        status = lr.get("status")
        cross = cross_by_marker.get(marker, [])
        verdict = cross[0]["verdict"] if cross else None
        if status == "refuted":
            final = "suspected-bug"
            reason = "proof refuted (unsound transform or lifter mismatch)"
        elif status != "proved":
            final, reason = "unlifted", status or "unknown"
        elif verdict == "verified":
            final, reason = "verified", ""
        elif verdict == "bug":
            final, reason = "suspected-bug", "real-opt TV refuted"
        elif verdict in ("no-trigger",):
            final, reason = "conditional", "opt did not fire here"
        elif verdict is None:
            final, reason = "proved-only", "cross-validation unavailable"
        else:
            final, reason = "proved-only", f"tv={verdict}"
        transforms.append({"marker": marker, "pass": pass_of(marker), "status": final,
                           "faithful": lr.get("faithful"), "reason": reason,
                           "guards": lr.get("guards", [])})

    # unlifted (skip/unliftable) sites: counted but not in results
    lc = lift_report.get("counts", {})
    unlifted_sites = lc.get("skip", 0) + lc.get("unliftable", 0)

    passes: dict[str, dict] = {}
    for t in transforms:
        p = passes.setdefault(t["pass"], {"verified": 0, "conditional": 0,
                                          "proved-only": 0, "suspected-bug": 0,
                                          "unlifted": 0, "markers": []})
        p[t["status"]] = p.get(t["status"], 0) + 1
        p["markers"].append(t)
    total = len(transforms) + unlifted_sites
    verified = sum(1 for t in transforms if t["status"] == "verified")
    coverage = round(100.0 * verified / total, 1) if total else 0.0
    return {"passes": passes, "transforms": transforms,
            "totals": {"mined_sites": total, "verified": verified,
                       "unlifted_sites": unlifted_sites, "coverage_pct": coverage,
                       "suspected_bugs": sum(1 for t in transforms if t["status"] == "suspected-bug")}}


def render(dossier: dict) -> str:
    t = dossier["totals"]
    lines = ["O2T Pass Verification Dossier",
             "=" * 40,
             f"mined sites: {t['mined_sites']}   verified: {t['verified']}   "
             f"coverage: {t['coverage_pct']}%   suspected-bugs: {t['suspected_bugs']}",
             ""]
    for pname, p in sorted(dossier["passes"].items()):
        lines.append(f"pass {pname}:  verified={p['verified']} conditional={p['conditional']} "
                     f"proved-only={p['proved-only']} suspected-bug={p['suspected-bug']}")
        for m in p["markers"]:
            flag = "" if m["status"] == "verified" else f"  <- {m['reason']}"
            faith = "" if m.get("faithful") in (None, True) else " [UNFAITHFUL]"
            lines.append(f"    [{m['status']:13}] {m['marker']}{faith}{flag}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--findings", type=Path)
    src.add_argument("--mine", type=Path, metavar="SNIPPET")
    src.add_argument("--selftest", action="store_true")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--text", type=Path, help="write the human-readable dossier here")
    args = ap.parse_args()

    if shutil.which(args.z3_bin) is None:
        print(json.dumps({"status": "skipped", "reason": "z3 not found"}))
        return 0

    lift_cmd = [sys.executable, str(LIFT), "--z3-bin", args.z3_bin]
    if args.mine is not None:
        lift_cmd += ["--mine", str(args.mine)]
    else:
        lift_cmd += ["--findings", str(args.findings or DEFAULT_FINDINGS)]
    lift_report = run_json(lift_cmd)
    if lift_report.get("status") == "skipped":
        print(json.dumps(lift_report))
        return 0

    cross_report = {}
    if lift_report.get("results"):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(lift_report, tmp)
            lift_path = tmp.name
        cross_report = run_json([sys.executable, str(CROSS), "--transforms", lift_path,
                                 "--z3-bin", args.z3_bin])
        Path(lift_path).unlink(missing_ok=True)

    dossier = build_dossier(lift_report, cross_report)
    text = render(dossier)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(dossier, indent=2, sort_keys=True) + "\n")
    if args.text:
        args.text.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
