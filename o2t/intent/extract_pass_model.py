#!/usr/bin/env python3
"""Assemble per-function pass models from miner findings (miner-side branch extraction).

The miner already emits one finding per `if (guard) return rewrite` branch. This
groups findings by their enclosing FUNCTION (parsed from the source) and orders
them by line, reconstructing the whole fold function as an ordered branch sequence
-- the input code-lift A (cv-lift-pass-model) and B (cv-symexec-pass) need to run
on REAL pass source instead of a hand-written model.

Per branch:
    guard  <- predicate_source (match(OpN,m_X) -> OpN==val; OpA==OpB; isKnownNonZero
              -> X!=0; poison/one-use -> no value effect; unknown -> unmodeled)
    output <- rewrite_source builder lift (cv_lift_matcher)
    opcode <- the instruction the fold handles (marker -> operation)

Emits a symexec-format pass model per function and (optionally) runs symbolic
execution on it (cascade path conditions -> sound / dead-branch / miscompile).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "tools"
ROOT = HERE.parent
from o2t.registry import lift_matcher as lm  # noqa: E402
from o2t.registry.optimization_registry import BV_OP_FOR_OPERATION  # noqa: E402
from o2t.facts.value_tracking import assumption_guard_smt, fact_to_assumptions  # noqa: E402

SYMEXEC = HERE / "cv-symexec-pass.py"
DEFAULT_FACTS = ROOT / "constraints" / "semantic_facts.json"
DEFAULT_FINDINGS = ROOT / "tests" / "fixtures" / "foldadd_branches.jsonl"
DEFAULT_MINER = ROOT / "build-clang-tools" / "cv-mine-pass-source-ast"


def run_miner(snippet: Path, miner: Path) -> list[dict]:
    proc = subprocess.run([str(miner), str(snippet)], capture_output=True, text=True, cwd=str(ROOT))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else data.get("findings", [])

MASK = (1 << 32) - 1
CONST_VAL = {"m_Zero": 0, "m_One": 1, "m_AllOnes": MASK}
# A function definition starts at column 0 with a return type; this excludes
# indented control flow (if/while/for) which otherwise looks like `name(...) {`.
FUNC_RE = re.compile(r"^[A-Za-z_][\w\s:*&<>]*?\b(\w+)\s*\([^;{}]*\)\s*\{", re.M)


def marker_opcode(facts_path: Path) -> dict[str, str]:
    data = json.loads(facts_path.read_text())
    records = data if isinstance(data, list) else data.get("records", [])
    out = {}
    for r in records:
        if isinstance(r, dict) and isinstance(r.get("semantic_facts"), dict):
            out[r["marker"]] = str(r["semantic_facts"].get("operation") or "")
    return out


def function_at(source: str, line: int) -> str:
    best, name = -1, "?"
    for m in FUNC_RE.finditer(source):
        start = source[:m.start()].count("\n") + 1
        if start <= line and start > best:
            best, name = start, m.group(1)
    return name


def analysis_fact_clauses(clause: str):
    """Lower one LLVM ValueTracking predicate to symexec guard clauses, or None.

    The recognition and SMT encoding live in `o2t.facts.value_tracking`
    (the single source of truth shared with `formal_ir.assumption_to_smt`), so the
    symexec cascade discharge and the intent-validation pipeline cannot drift. Each
    fact becomes a raw-SMT guard leaf carrying its free variables for declaration.
    """
    assumptions = fact_to_assumptions(clause)
    if assumptions is None:
        return None
    clauses = []
    for assumption in assumptions:
        lowered = assumption_guard_smt(assumption)
        if lowered is None:
            return None
        smt, variables = lowered
        clauses.append({"op": "smt", "text": smt, "vars": variables})
    return clauses


def predicate_to_guard(predicate: str):
    """Return (guard_node, unmodeled_clauses)."""
    clauses, unmodeled = [], []
    for c in (x.strip() for x in predicate.split("&&")):
        if not c:
            continue
        m = re.search(r"match\(\s*(\w+)\s*,\s*(m_\w+)", c)
        if m and m.group(2) in CONST_VAL:
            clauses.append({"op": "eq", "args": [{"op": "var", "name": m.group(1)},
                                                 {"op": "bvconst", "bits": 32, "value": CONST_VAL[m.group(2)]}]})
            continue
        eq = re.match(r"^(\w+)\s*==\s*(\w+)$", c)
        if eq:
            clauses.append({"op": "eq", "args": [{"op": "var", "name": eq.group(1)},
                                                 {"op": "var", "name": eq.group(2)}]})
            continue
        nz = re.search(r"isKnownNonZero\(\s*&?(\w+)", c)
        if nz:
            clauses.append({"op": "ne", "args": [{"op": "var", "name": nz.group(1)},
                                                 {"op": "bvconst", "bits": 32, "value": 0}]})
            continue
        facts = analysis_fact_clauses(c)
        if facts is not None:
            clauses.extend(facts)
            continue
        if re.search(r"hasPoisonGeneratingFlags|hasOneUse|hasNUses", c):
            continue  # modeled, no value effect
        unmodeled.append(c)
    if not clauses:
        clauses = [{"op": "eq", "args": [{"op": "bvconst", "bits": 32, "value": 0},
                                         {"op": "bvconst", "bits": 32, "value": 0}]}]  # always true
    guard = clauses[0] if len(clauses) == 1 else {"op": "and", "args": clauses}
    return guard, unmodeled


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.exists() else (ROOT / path)


def build_models(findings: list[dict], opcode_map: dict[str, str]) -> list[dict]:
    groups: dict[tuple, list] = {}
    for f in findings:
        # Prefer the miner's native enclosing-function field (AST-accurate); fall
        # back to regex source parsing only for findings that lack it.
        fn = f.get("function")
        if not fn:
            src = resolve(f["file"]).read_text() if resolve(f["file"]).exists() else ""
            fn = function_at(src, int(f.get("line", 0))) if src else "?"
        groups.setdefault((f["file"], fn), []).append(f)
    models = []
    for (file, fn), fs in groups.items():
        # Order by the miner's native branch_index when present (AST cascade order);
        # fall back to source line.
        def order_key(x):
            bi = x.get("branch_index")
            return (0, bi) if isinstance(bi, int) and bi >= 0 else (1, int(x.get("line", 0)))
        fs.sort(key=order_key)
        # Prefer the opcode MINED FROM SOURCE (the function the fold lives in) over
        # the marker registry: a branch's marker may be borrowed from a different
        # operation (e.g. an `xor-self` shaped branch inside an add fold), but the
        # instruction a fold handles is a property of the function, not the branch.
        op = next((x.get("opcode") for x in fs if x.get("opcode")), "") \
            or next((opcode_map.get(x["marker"]) for x in fs if opcode_map.get(x["marker"])), "")
        bvop = BV_OP_FOR_OPERATION.get(op)
        if not bvop:
            continue
        branches = []
        for x in fs:
            guard, _ = predicate_to_guard(str(x.get("predicate_source") or ""))
            rewrite = re.sub(r"^\s*return\s+", "", str(x.get("rewrite_source") or "")).rstrip(";").strip()
            try:
                output = lm.lift_builder_expr(rewrite)
            except lm.MatcherError:
                continue
            branches.append({"name": f"{x['marker'].split('.')[-1]}@{x.get('line')}",
                             "guard": guard, "output": output})
        if branches:
            models.append({"function": fn, "file": file, "opcode": bvop,
                           "operands": ["Op0", "Op1"], "branches": branches})
    return models


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--findings", type=Path)
    src.add_argument("--mine", type=Path, metavar="SNIPPET",
                     help="run the real miner (native function field) then extract")
    src.add_argument("--selftest", action="store_true")
    ap.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    ap.add_argument("--symexec", action="store_true", help="run symbolic execution on each model")
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    if args.mine is not None:
        if not args.miner.exists():
            print(json.dumps({"status": "skipped", "reason": "miner not built"}))
            return 0
        findings = run_miner(args.mine, args.miner)
    else:
        findings_path = DEFAULT_FINDINGS if args.selftest else args.findings
        if findings_path is None:
            ap.error("provide --findings, --mine, or --selftest")
        findings = [json.loads(l) for l in findings_path.read_text().splitlines() if l.strip()]
    models = build_models(findings, marker_opcode(DEFAULT_FACTS))

    out = {"models": models, "model_reports": []}
    if args.symexec or args.selftest or args.mine is not None:
        z3_bin = shutil.which(args.z3_bin)
        if z3_bin is None:
            print(json.dumps({"status": "skipped", "reason": "z3 not found", "models": len(models)}))
            return 0
        for model in models:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
                json.dump(model, tmp)
                mp = tmp.name
            rep = run_symexec(mp, z3_bin)
            Path(mp).unlink(missing_ok=True)
            out["model_reports"].append(rep)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    summary = {"functions": len(models),
               "reports": [{"function": r.get("function"), "counts": r.get("counts"),
                            "dead_branches": r.get("dead_branches"), "miscompiles": r.get("miscompiles"),
                            "exhaustive": r.get("exhaustive")} for r in out["model_reports"]]}
    print(json.dumps(summary, sort_keys=True))
    for model in models:
        print(f"  function {model['function']}: {len(model['branches'])} branches, opcode={model['opcode']}",
              file=sys.stderr)
    return 0


def run_symexec(model_path: str, z3_bin: str) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        rep = Path(tmp.name)
    try:
        subprocess.run([sys.executable, str(SYMEXEC), "--model", model_path,
                        "--z3-bin", z3_bin, "--report", str(rep)], capture_output=True, text=True)
        return json.loads(rep.read_text()) if rep.stat().st_size else {}
    except (OSError, json.JSONDecodeError):
        return {}
    finally:
        rep.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
