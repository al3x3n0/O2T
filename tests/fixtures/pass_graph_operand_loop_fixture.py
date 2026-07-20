#!/usr/bin/env python3
"""Bounded loops over an operand list: recovering a fold whose iterations are NOT independent.

The prior loop handling treats a `for (Instruction &I : BB)` header as transparent -- sound only when
each iteration is an INDEPENDENT per-instruction fold. A loop over an instruction's OWN operand list,
where the guard is quantified over every operand, fell outside that and declined. The canonical case is
`SimplifyPHINode`: `for (Value *In : PN->incoming_values()) if (In != First) return nullptr;` then
`phi [x,x,..,x] -> First` -- the guard is `forall i>0. op_i == op_0` over an UNBOUNDED list.

This phase recovers that class at a BOUNDED arity -- modeling the phi as a nondeterministic merge over
k operands (`ite(s_i, op_i, ...)`, selectors universally quantified = "the phi may take any incoming
value") that must collapse to `op_0` under the recovered pairwise-equality guard -- and CORROBORATES
that the verdict is arity-UNIFORM (so it generalizes to all N), mirroring how `corroborate_widths`
(phase 28) licenses a bv32 verdict as width-uniform. An under-recovered guard is sound at arity 2 (the
single equality IS the whole guard) but REFUTES at arity 3+, and the corroboration catches exactly that.

Needs z3. Uses reconcile_solver (a second SMT solver, symbolic) -- not the concrete `reconcile`, whose
brute-force enumeration does not scale to the merge's selector variables.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg
from o2t import mini_alive as ma


PHI_COLLAPSE = """Value *simplifyPHINode(PHINode *PN){
  Value *First = PN->getIncomingValue(0);
  for (Value *In : PN->incoming_values())
    if (In != First) return nullptr;
  return replaceInstUsesWith(*PN, First);
}"""


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_operand_loop_fixture: z3 not found, skipped")
        return 0

    # 1. The phi-all-same collapse is RECOVERED from the loop-over-operands source and PROVED. The
    #    recovered precondition is the pairwise equality of every operand to the representative -- the
    #    quantified guard the flat/independent-iteration path cannot express.
    pair = pg.recover_from_function(PHI_COLLAPSE)
    assert pair is not None, "phi-all-same collapse loop must be recovered"
    assert ma.prove(pair, z3)[0] == "proved", "recovered phi-collapse must prove"
    assert pair["assumptions"] == [
        {"op": "rel", "predicate": "eq", "left": "op0", "right": "op1"},
        {"op": "rel", "predicate": "eq", "left": "op0", "right": "op2"},
    ], pair["assumptions"]

    # 2. It proves at every bounded arity -- 2, 3, 4 operands -- because the identity is arity-uniform.
    for k in (2, 3, 4):
        assert ma.prove(pg._phi_collapse_obligation(k), z3)[0] == "proved", ("phi-collapse must prove", k)

    # 3. TEETH: an UNDER-recovered guard (the merge over 3 operands, but the guard only equates op1 to
    #    the representative, not op2) is unsound -- a selector path is free to pick the differing op2 --
    #    and REFUTES with a concrete witness. Recovering the RIGHT universal guard is load-bearing.
    status, cex = ma.prove(pg._phi_collapse_obligation(3, drop_equalities=[2]), z3)
    assert status == "refuted" and cex, ("under-recovered guard must refute", status)
    assert cex["inputs"]["op2"] != cex["inputs"]["op0"], ("witness must exhibit the free operand", cex)

    # 4. ARITY corroboration: the sound fold's verdict is uniform across {2,3,4} -> `proved`. This is
    #    what licenses the "for all N" claim from a bounded proof.
    sound = pg.corroborate_arity(lambda k: pg._phi_collapse_obligation(k), z3)
    assert sound["agree"] and sound["status"] == "proved", sound

    # 5. ...and it CATCHES an under-recovery that a single (arity-2) proof would have missed: a guard
    #    that only equates the FIRST operand pair is the whole guard at arity 2 (proved) but drops a
    #    value-relevant equality at arity 3+ (refuted). The divergence is flagged `arity-specific`.
    buggy = pg.corroborate_arity(lambda k: pg._phi_collapse_obligation(k, drop_equalities=range(2, k)), z3)
    assert not buggy["agree"] and buggy["status"] == "arity-specific", buggy
    assert buggy["verdicts"] == {2: "proved", 3: "refuted", 4: "refuted"}, buggy["verdicts"]

    # 6. Second-solver cross-check: an independent SMT solver agrees the recovered obligation is sound
    #    (or skips cleanly when bitwuzla is absent). The concrete `reconcile` is intractable here (its
    #    brute force enumerates the merge's selector variables), so the symbolic solver is the oracle.
    rec = pg.reconcile_solver(pair, z3)
    assert rec["agree"], rec

    # 7. SOUND DECLINE (never mis-model): only the pure all-equal collapse loop is recovered. A worklist
    #    push, a side-effecting Builder statement in the body, and a guard comparing against an EXTERNAL
    #    value (not the replacement operand) each decline -- the fold is bounded, not silently modeled.
    worklist = ("Value *f(Instruction *I){\n"
                "  for (User *U : I->users())\n"
                "    Worklist.push(U);\n"
                "  return replaceInstUsesWith(*I, First);\n}")
    sidefx = ("Value *f(PHINode *PN){\n"
              "  Value *First = PN->getIncomingValue(0);\n"
              "  for (Value *In : PN->incoming_values()) {\n"
              "    if (In != First) return nullptr;\n"
              "    Builder.CreateAdd(In, First);\n"
              "  }\n"
              "  return replaceInstUsesWith(*PN, First);\n}")
    external = ("Value *f(PHINode *PN){\n"
                "  Value *First = PN->getIncomingValue(0);\n"
                "  for (Value *In : PN->incoming_values())\n"
                "    if (In != Other) return nullptr;\n"
                "  return replaceInstUsesWith(*PN, First);\n}")
    for src in (worklist, sidefx, external):
        assert pg.recover_operand_loop(src) is None, ("out-of-fragment operand loop must decline", src)

    # 8. NO REGRESSION: a basic-block instruction loop has INDEPENDENT iterations -- it is not an
    #    operand-list collapse and is left to the transparent-header path, still recovered as before.
    bbloop = ("Value *f(BasicBlock &BB){\n"
              "  for (Instruction &I : BB) {\n"
              "    Value *X;\n"
              "    if (match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One())))\n"
              "      replaceInstUsesWith(I, X);\n"
              "  }\n"
              "  return nullptr; }")
    assert pg.recover_operand_loop(bbloop) is None, "bb instruction loop is not an operand-collapse loop"
    assert pg.recover_from_function(bbloop) is not None, "bb instruction loop still recovered by the header path"

    print("pass_graph_operand_loop_fixture OK: a loop over an operand list (SimplifyPHINode's "
          "phi [x,x,..,x] -> x) is recovered with its quantified all-equal guard and proved at bounded "
          "arity {2,3,4}; an under-recovered guard refutes with a witness and is flagged arity-specific "
          "by the corroboration (caught where a single arity-2 proof would miss it); a second solver "
          "agrees; and worklist / side-effecting / external-guard loops decline while an independent "
          "basic-block loop is unaffected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
