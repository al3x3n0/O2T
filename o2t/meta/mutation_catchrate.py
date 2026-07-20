#!/usr/bin/env python3
"""E2: mutation catch-rate -- aggregate every tier's teeth into one measured table.

A proof is only as strong as what its refutation catches. O2T's teeth exist in three independent
tiers, each already gated point-wise; this rolls them into the aggregate table the paper's E2 asks
for, and asserts the one invariant that matters: EVERY seeded corruption is caught, no survivors.

- deep contracts: `proof_audit.run_audit` -- single-point corruptions of the family SMT models
  (swap a vector lane, drop a guard, flip a condition, break associativity, expose an
  initializer). Each mutant must be refuted with a witness; a surviving mutant is a teeth gap.
- recovery: `ablation.run_ablation` (E7) -- misrecovery classes seeded into recovered obligations.
- registry intents: cv-check-negative-intents --mutate -- each sound scalar intent is perturbed
  (`after + 1`) and must then be rejected.

Witness MINIMALITY (trip count / |params|) is a loop-track property of the CEGAR witnesses, not of
these point-mutation refutations; it is measured with the loop fixtures (E1/E5), stated here so E2
is not overclaimed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from o2t.intent import ablation
from o2t.meta.proof_audit import run_audit

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"


def _deep_contract_tier(z3: str) -> dict:
    audit = run_audit(z3)
    by_family: dict[str, dict] = {}
    witnessed = 0
    for row in audit["rows"]:
        fam = by_family.setdefault(row["family"], {"contracts": 0, "mutants": 0, "killed": 0})
        fam["contracts"] += 1
        for m in row["mutants"]:
            fam["mutants"] += 1
            if m["killed"]:
                fam["killed"] += 1
                witnessed += 1                            # _killed requires a witness
    return {"by_family": by_family,
            "mutants": audit["mutants"], "killed": audit["killed"],
            "survivors": audit["survivors"], "premises_satisfiable": audit["premises_satisfiable"],
            "witnessed": witnessed}


def _recovery_tier(z3: str) -> dict:
    abl = ablation.run_ablation(z3)
    classes, caught = 0, 0
    for kind, row in abl["matrix"].items():
        applicable = [v for f, v in row.items()
                      if f != "member_verdicts" and v != "not-applicable"]
        if not applicable:
            continue
        classes += 1
        if all(v != "ESCAPED" for v in applicable):
            caught += 1
    return {"classes": classes, "caught": caught, "escapes": abl["escapes"]}


def _registry_tier(z3: str) -> dict:
    tool = str(TOOLS / "cv-check-negative-intents.py")
    with __import__("tempfile").NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rep = Path(tf.name)
    try:
        subprocess.run([sys.executable, tool, "--mutate", "--z3", z3, "--report", str(rep)],
                       capture_output=True, text=True)
        data = json.loads(rep.read_text()) if rep.stat().st_size else {}
    except (OSError, json.JSONDecodeError):
        return {"available": False}
    finally:
        rep.unlink(missing_ok=True)
    results = data.get("results", [])
    muts = [r for r in results if r.get("kind") == "mutation"]
    negs = [r for r in results if r.get("kind") == "negative"]
    return {"available": True,
            "mutations": len(muts), "mutations_rejected": sum(1 for r in muts if r["ok"]),
            "negatives": len(negs), "negatives_rejected": sum(1 for r in negs if r["ok"])}


def run(z3: str) -> dict:
    deep = _deep_contract_tier(z3)
    recovery = _recovery_tier(z3)
    registry = _registry_tier(z3)
    total_seeded = deep["mutants"] + recovery["classes"] + registry.get("mutations", 0)
    total_caught = deep["killed"] + recovery["caught"] + registry.get("mutations_rejected", 0)
    return {"deep_contracts": deep, "recovery": recovery, "registry": registry,
            "total_seeded": total_seeded, "total_caught": total_caught,
            "survivors": deep["survivors"] + recovery["escapes"],
            "note": "witness minimality (trip count / |params|) is a loop-track CEGAR property, "
                    "measured with the loop fixtures, not these point-mutation refutations"}


def render(r: dict) -> str:
    lines = ["== E2: mutation catch-rate (all teeth tiers) =="]
    d = r["deep_contracts"]
    lines.append(f"deep contracts: {d['killed']}/{d['mutants']} mutants killed, "
                 f"{len(d['survivors'])} survivors, premises-SAT={d['premises_satisfiable']}")
    for fam, v in sorted(d["by_family"].items()):
        lines.append(f"  {fam:18s} {v['killed']}/{v['mutants']}  ({v['contracts']} contracts)")
    rc = r["recovery"]
    lines.append(f"recovery (E7): {rc['caught']}/{rc['classes']} misrecovery classes caught, "
                 f"{len(rc['escapes'])} escapes")
    rg = r["registry"]
    if rg.get("available"):
        lines.append(f"registry intents: {rg['mutations_rejected']}/{rg['mutations']} perturbed "
                     f"intents rejected; {rg['negatives_rejected']}/{rg['negatives']} negatives")
    lines.append(f"TOTAL: {r['total_caught']}/{r['total_seeded']} seeded corruptions caught, "
                 f"{len(r['survivors'])} survivors")
    lines.append(r["note"])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    import shutil
    ap = argparse.ArgumentParser(description="E2: aggregate mutation catch-rate across teeth tiers")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    z3 = shutil.which(args.z3_bin)
    if z3 is None:
        print("cv-mutation-catchrate: z3 required", file=sys.stderr)
        return 2
    r = run(z3)
    if args.report:
        args.report.write_text(json.dumps(r, indent=2) + "\n")
    print(render(r), end="")
    return 1 if r["survivors"] else 0


if __name__ == "__main__":
    sys.exit(main())
