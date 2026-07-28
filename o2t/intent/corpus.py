#!/usr/bin/env python3
"""E6: Pass-IR corpus coverage — run the structural recovery over a real pass-source corpus.

Measures, per candidate fold function, what the recovery fragment actually does on upstream code:
`recovered-proved` (and cross-checked), `recovered-refuted` (with a witness), `recovered-untrusted`
(prover/reconciler disagreement or unsupported obligation -- NEVER counted as proved), or
`declined` with a heuristic reason bucket. The headline invariant this run is designed to check is
**zero false proofs**: everything outside the modeled fragment declines, and every `proved` must
survive the concrete reconciliation cross-check. Low recovery rates on upstream code are an honest
RESULT (the fragment is scoped), not a failure -- the decline taxonomy is the coverage frontier.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from o2t.intent import pass_graph as pg
from o2t.mine import clang_tree as ct

# A candidate fold FUNCTION: returns Value*/Instruction* (free, static, or out-of-line method).
_FUNC_RE = re.compile(
    r"(?:^|\n)\s*(?:static\s+)?(?:llvm::)?(?:Value|Instruction)\s*\*\s*"
    r"(?:[A-Za-z_]\w*::)?([A-Za-z_]\w*)\s*\(", re.M)

DECLINE_BUCKETS = (
    "no-match-call",          # never inspects a PatternMatch matcher -- outside the fragment by design
    "no-riuw-rewrite",        # matches, but the rewrite is not a replaceInstUsesWith form
    "loop-over-ir",           # iterates IR in a shape the bounded loop rungs do not model
    "in-fragment-shape",      # has match + replaceInstUsesWith yet still declines (guard/rewrite shape)
)


def extract_functions(source_text: str, max_lines: int | None = None) -> list[dict]:
    """Candidate fold functions with balanced-brace bodies. `max_lines` skips (and counts) bodies
    above the bound -- reported, never silently dropped."""
    out = []
    for m in _FUNC_RE.finditer(source_text):
        brace = source_text.find("{", m.end())
        semi = source_text.find(";", m.end())
        if brace < 0 or (0 <= semi < brace):          # declaration, not definition
            continue
        try:
            body, end = pg._balanced_brace(source_text, brace)
        except pg.Unsupported:
            continue
        full = source_text[m.start():end]
        lines = full.count("\n") + 1
        out.append({"name": m.group(1), "full": full, "lines": lines,
                    "skipped-oversize": bool(max_lines and lines > max_lines)})
    return out


def _decline_bucket(full: str) -> str:
    if "match(" not in full:
        return "no-match-call"
    if "replaceInstUsesWith" not in full:
        return "no-riuw-rewrite"
    if re.search(r"\bfor\s*\(|\bwhile\s*\(", full):
        return "loop-over-ir"
    return "in-fragment-shape"


def _rung(full: str) -> str:
    """Which recovery-ladder rung produced the obligation (labeling only)."""
    if pg.recover_operand_loop(full) is not None:
        return "operand-loop"
    if pg.recover_reduction_loop(full) is not None:
        return "reduction-loop"
    if "replaceInstUsesWith" not in full:
        return "return-form"                # phase 36: the upstream return-the-replacement contract
    return "function-path"


def _arm_outcome(arm: dict, z3: str) -> dict:
    """Prove + cross-check one fold ARM. The zero-false-proof discipline: `proved` requires BOTH
    the z3 verdict and (where the concrete engine covers the obligation) reconcile agreement; any
    disagreement or unsupported verdict lands in `recovered-untrusted`. The zero-false-REFUTATION
    discipline: a STANDALONE arm (arm > 0) is proved over a superset of its reachable inputs, so
    its refutation is `refuted-standalone` -- the witness may be excluded by an earlier arm; it is
    an advisory frontier marker, NEVER a pass-level unsoundness claim."""
    out = {"arm": arm["arm"], "standalone": arm["standalone"]}
    from o2t import mini_alive as ma
    status, cex = ma.prove(arm, z3)
    if status == "refuted":
        out["outcome"] = "refuted-standalone" if arm["standalone"] else "recovered-refuted"
        out["witness"] = bool(cex)
        return out
    if status != "proved":
        out.update({"outcome": "recovered-untrusted", "reason": f"prove: {status}"})
        return out
    rec = pg.reconcile(arm, z3)
    if not rec.get("agree", False):
        out.update({"outcome": "recovered-untrusted", "reason": "reconcile disagreement",
                    "reconcile": {k: rec.get(k) for k in ("z3", "concrete", "checked")}})
        return out
    out.update({"outcome": "recovered-proved", "reconcile": rec.get("concrete", "skipped")})
    return out


def run_function(fn: dict, z3: str | None, cpp_path=None, includes=None,
                 clang_bin: str = "clang", fragments: bool = False) -> dict:
    """Slice one candidate function into its fold arms and drive each to a verdict.

    Recovery goes through the CLANG AST, not O2T's own reading of the source text: the obligation
    that gets proved is built from the real compiler's parse of the real code. That requires the
    pass's compile context (`includes` -- the LLVM public headers plus the pass's lib-internal header
    dir), which is why it is a required input rather than an option; there is deliberately no text
    fallback, because a second recovery path is the dual-path drift this replaces."""
    result = {"function": fn["name"], "lines": fn["lines"]}
    if fn["skipped-oversize"]:
        result.update({"outcome": "skipped-oversize"})
        return result
    arms = (pg.recover_folds_from_function(fn["full"]) if fragments else
            ct.recover_folds_from_source_file(str(cpp_path), fn["name"], list(includes or []),
                                              clang_bin=clang_bin))
    if not arms:
        result.update({"outcome": "declined", "bucket": _decline_bucket(fn["full"])})
        return result
    result["rung"] = _rung(fn["full"])
    if z3 is None:
        result.update({"outcome": "recovered-unproved", "reason": "no z3", "arms": len(arms)})
        return result
    result["arms"] = [_arm_outcome(a, z3) for a in arms]
    result["outcome"] = "recovered"
    return result


class MissingCompileContext(RuntimeError):
    """Real-source recovery needs the pass's compile context; there is no text fallback."""


def run_corpus(paths: list[Path], z3: str | None, max_lines: int | None = None,
               includes=None, clang_bin: str = "clang", fragments: bool = False) -> dict:
    """Sweep a pass-source corpus.

    TWO RECOVERY MODES, and which one produced a verdict is recorded in the report so a claim can
    never be attributed to the wrong reader:

    * DEFAULT -- real pass source. Obligations are recovered from the CLANG AST, so what gets proved
      is built from the compiler's parse of code that actually compiles. `includes` (the pass's
      compile context) is therefore REQUIRED, and its absence is an error rather than a quiet
      downgrade: a silent second reader is the dual-path drift this design removes.

    * `fragments=True` -- TEST-ONLY, and it must be asked for by name. O2T's shape fixtures exercise
      recovery on C++ FRAGMENTS that deliberately do not compile (no headers, undeclared types), and
      reading those is exactly what the text front-end can do and the AST front-end cannot: on the E6
      synthetic corpus the text path recovers 6 of 11 functions and the AST path 0 of 11, because the
      corpus is not a translation unit. This mode keeps that shape-level testing possible; it is not
      a fallback, cannot be reached by omitting `includes`, and must never carry a verdict about a
      real pass.
    """
    if fragments and includes:
        raise ValueError("fragment mode reads source that does not compile; a compile context is "
                         "meaningless there -- pass one or the other, not both")
    if not fragments and not includes:
        raise MissingCompileContext(
            "pass-source recovery needs the compile context: pass -I<dir> for the LLVM public "
            "headers and for the pass's own lib-internal header directory. Recovery reads the "
            "clang AST of the real source; there is no text fallback. (Shape fixtures that "
            "deliberately use non-compiling fragments must pass fragments=True explicitly.)")
    files = []
    for p in paths:
        files.extend(sorted(p.rglob("*.cpp")) if p.is_dir() else [p])
    per_fn, per_file = [], []
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError as exc:
            per_file.append({"file": str(f), "error": str(exc)})
            continue
        fns = extract_functions(text, max_lines)
        for fn in fns:
            r = run_function(fn, z3, cpp_path=f, includes=includes, clang_bin=clang_bin,
                             fragments=fragments)
            r["file"] = f.name
            per_fn.append(r)
        per_file.append({"file": str(f), "functions": len(fns)})
    counts: dict[str, int] = {}          # function-level outcomes (declined / recovered / skipped)
    arm_counts: dict[str, int] = {}      # fold-arm-level outcomes
    buckets: dict[str, int] = {}
    rungs: dict[str, int] = {}
    total_arms = 0
    for r in per_fn:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        if r["outcome"] == "declined":
            buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
        if "rung" in r:
            rungs[r["rung"]] = rungs.get(r["rung"], 0) + 1
        # `arms` is a LIST of per-arm outcomes when proving ran, but the no-z3 branch stores a COUNT
        # instead. Iterating that crashes the sweep -- a pre-existing bug that never fired because the
        # gate always has z3. Skip the arm accounting when there are no per-arm outcomes to account.
        for arm in (r.get("arms") if isinstance(r.get("arms"), list) else []):
            total_arms += 1
            arm_counts[arm["outcome"]] = arm_counts.get(arm["outcome"], 0) + 1
    return {"files": per_file, "functions": len(per_fn), "outcomes": counts,
            "recovery": "fragment-text (test-only)" if fragments else "clang-ast",
            "fold_arms": total_arms, "arm_outcomes": arm_counts,
            "decline_buckets": buckets, "rungs": rungs, "results": per_fn,
            "invariant": "every proved arm survived the reconcile cross-check (disagreements land "
                         "in recovered-untrusted, never proved); a standalone arm's refutation is "
                         "advisory (earlier-arm exclusions unmodeled), never a pass-level claim"}


def render_table(report: dict) -> str:
    lines = ["== E6: Pass-IR corpus coverage ==",
             f"files: {len(report['files'])}   candidate fold functions: {report['functions']}"
             f"   fold arms recovered: {report.get('fold_arms', 0)}",
             "function outcomes:"]
    for k in sorted(report["outcomes"]):
        lines.append(f"  {k:22s} {report['outcomes'][k]}")
    if report.get("arm_outcomes"):
        lines.append("fold-arm outcomes:")
        for k in sorted(report["arm_outcomes"]):
            lines.append(f"  {k:22s} {report['arm_outcomes'][k]}")
    if report["decline_buckets"]:
        lines.append("decline taxonomy (the coverage frontier):")
        for k in DECLINE_BUCKETS:
            if k in report["decline_buckets"]:
                lines.append(f"  {k:22s} {report['decline_buckets'][k]}")
    if report["rungs"]:
        lines.append("recovered, by ladder rung:")
        for k, v in sorted(report["rungs"].items()):
            lines.append(f"  {k:22s} {v}")
    lines.append(report["invariant"])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E6: Pass-IR recovery over a pass-source corpus")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--max-fn-lines", type=int, default=None,
                    help="skip (and count) function bodies above this bound")
    ap.add_argument("-I", "--cxx-include", action="append", default=[], dest="cxx_includes",
                    metavar="DIR",
                    help="compile-context include dir (repeatable): the LLVM public headers, and the "
                         "pass's own lib-internal header dir. REQUIRED -- recovery reads the clang AST")
    ap.add_argument("--clang-bin", default="clang")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--summary-text", type=Path)
    args = ap.parse_args(argv)
    import shutil
    z3 = shutil.which(args.z3_bin)
    report = run_corpus(args.paths, z3, args.max_fn_lines,
                        includes=args.cxx_includes, clang_bin=args.clang_bin)
    text = render_table(report)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    if args.summary_text:
        args.summary_text.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
