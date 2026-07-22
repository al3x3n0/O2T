#!/usr/bin/env python3
"""Enrichment agent CLI: an LLM proposes missing instruction semantics; lli decides.

Point it at a `.ll` corpus. It diagnoses whole-function-TV `unsupported` declines, asks the LLM
(`--llm-command`, e.g. `claude -p --output-format json`) to propose each missing instruction's SMT
semantics, VALIDATES every proposal against `lli` execution, installs the survivors, and reports the
reach lift. The LLM proposes; an independent oracle decides -- a wrong model is rejected before it can
enable a false proof. See o2t/agent/enrich_agent.py.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from o2t.agent.llm import LLMClient  # noqa: E402
from o2t.agent import enrich_agent as ea  # noqa: E402
from o2t.frontend import tv_matrix as tv  # noqa: E402

_HB_LLI = "/opt/homebrew/opt/llvm@18/bin/lli"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ll", type=Path, help="LLVM .ll corpus")
    ap.add_argument("--llm-command", required=True,
                    help="shell command: JSON request on stdin -> JSON reply on stdout "
                         "(e.g. 'claude -p --output-format json')")
    ap.add_argument("--budget", type=int, default=25)
    ap.add_argument("--z3-bin", default="z3")
    ap.add_argument("--opt-bin", default="opt")
    ap.add_argument("--lli-bin", default="lli")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)

    z3 = shutil.which(args.z3_bin)
    opt = tv._resolve_opt(args.opt_bin)
    lli = shutil.which(args.lli_bin) or (_HB_LLI if Path(_HB_LLI).exists() else None)
    if z3 is None or opt is None or lli is None:
        print("cv-enrich-agent: z3, opt, and lli (18) required", file=sys.stderr)
        return 2
    client = LLMClient(command=args.llm_command, budget=args.budget)
    report = ea.run(args.ll.read_text(), client, z3, lli, opt)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("diagnosed", "installed", "proved_before",
                                             "proved_after")}, indent=2))
    for e in report["enrichments"]:
        print(f"  [{e['status']}] {e.get('proposal', e['instruction'])}"
              + (f"  ({e['checked']} lli-checks)" if e.get("checked") else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
