#!/usr/bin/env python3
"""Cover the Pass-IR compositional fold recovery (o2t/intent/pass_graph.py).

The legacy source-intent path keys formal IR off a flat (operation, identity, rewrite) triple, so it
declines compound folds. pass_graph recovers the fold STRUCTURALLY from the matcher tree + rewrite
DFG. This pins: (1) a NESTED fold the triple cannot express proves; (2) `or-self` (X|X -> X), a fold
with no registry marker, is recovered with zero registry coupling; (3) a multi-var Builder DFG
rewrite proves; (4) a WRONG recovered fold is refuted with a witness (teeth); (5) an unmodeled
matcher is declined (None), never mis-modeled. Recovered obligations are discharged by the existing
prover, so they inherit its premise-SAT / teeth / cross-check trust layer. Needs z3."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg
from o2t import mini_alive as ma


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_fixture: z3 not found, skipped")
        return 0

    def prove(pred, rw):
        pair = pg.recover_pair(pred, rw)
        assert pair is not None, ("expected a recovered fold", pred, rw)
        return pair, ma.prove(pair, z3)

    # 1) NESTED compositional fold -- (X+0)*1 -> X -- inexpressible as a single (op,identity) triple.
    pair, (status, _) = prove("match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))",
                              "return replaceInstUsesWith(I, X);")
    assert status == "proved", ("nested fold not proved", status, pair)
    assert pair["before"]["op"] == "bvmul" and pair["before"]["args"][0]["op"] == "bvadd", pair["before"]

    # 2) or-self (X|X -> X): a fold with NO registry marker, recovered structurally (zero coupling).
    _, (status, _) = prove("match(&I, m_Or(m_Value(X), m_Deferred(X)))",
                           "return replaceInstUsesWith(I, X);")
    assert status == "proved", ("or-self not recovered/proved", status)

    # 3) multi-variable rewrite through a Builder.Create* DFG subtree.
    pair, (status, _) = prove("match(&I, m_And(m_Value(X), m_Value(Y)))",
                              "return replaceInstUsesWith(I, Builder.CreateAnd(X, Y));")
    assert status == "proved" and pair["variables"] == ["x", "y"], (status, pair["variables"])

    # 4) TEETH: a wrong recovered fold (X - Y -> X) must be refuted with a witness.
    pair, (status, cex) = prove("match(&I, m_Sub(m_Value(X), m_Value(Y)))",
                                "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("a wrong recovered fold was not refuted", status, cex)

    # 5) SOUND boundary: an unmodeled matcher is declined (None), never mis-modeled.
    assert pg.recover_pair("match(&I, m_Select(m_Value(C), m_Value(X), m_Value(Y)))",
                           "return replaceInstUsesWith(I, X);") is None
    assert pg.recover_pair("match(&I, m_Intrinsic<Intrinsic::ctpop>(m_Value(X)))",
                           "return replaceInstUsesWith(I, X);") is None

    # 6) PRECONDITION RECOVERY (phase 1): the guard's analysis queries become the premise the
    #    equivalence is proved UNDER. `sdiv X,Y -> udiv X,Y` is UNSOUND in general but sound when
    #    both operands are known non-negative -- the recovered guard is load-bearing.
    sdiv = "match(&I, m_SDiv(m_Value(X), m_Value(Y)))"
    udiv = "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"
    _, (status, cex) = prove(sdiv, udiv)                                   # no guard
    assert status == "refuted" and cex, ("unguarded sdiv->udiv must refute", status)
    pair, (status, _) = prove(sdiv + " && isKnownNonNegative(X) && isKnownNonNegative(Y)", udiv)
    assert status == "proved", ("guarded sdiv->udiv must prove", status)
    assert {(a["op"], a["name"]) for a in pair["assumptions"]} == {("cmp", "x"), ("cmp", "y")}, pair["assumptions"]
    # an INSUFFICIENT guard (only one operand constrained) must still refute -- teeth on completeness.
    _, (status, _) = prove(sdiv + " && isKnownNonNegative(X)", udiv)
    assert status == "refuted", ("insufficient guard should not prove", status)
    # a CONTRADICTORY recovered guard hits the premise-SAT vacuity gate -> not falsely proved.
    contradictory = pg.recover_pair(
        sdiv + " && isKnownNonNegative(X) && isKnownNegative(X) && isKnownNonNegative(Y)", udiv)
    assert ma.prove(contradictory, z3)[0] == "unsupported", "contradictory guard must be caught vacuous"
    # a value-irrelevant guard (hasOneUse) is dropped; an UNMODELED guard declines.
    _, (status, _) = prove("match(&I, m_Or(m_Value(X), m_Deferred(X))) && hasOneUse(Op0)",
                           "return replaceInstUsesWith(I, X);")
    assert status == "proved", ("value-irrelevant guard should be dropped", status)
    assert pg.recover_pair(sdiv + " && someUnknownAnalysis(X)", udiv) is None

    # 7) FUNCTION-LEVEL path condition (phase 1+): reconstruct the precondition from a real fold
    #    function's control flow -- early-return bailouts (`if (!G) return nullptr;` -> path gains G,
    #    De Morgan on `!A || !B`) plus positive guards -- and prove the fold UNDER it.
    def fn(src):
        pair = pg.recover_from_function(src)
        assert pair is not None, ("function recovery declined", src)
        return pair, ma.prove(pair, z3)

    sound_fn = ("Value *f(BinaryOperator &I){ Value *X,*Y;\n"
                "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
                "  if (!isKnownNonNegative(X) || !isKnownNonNegative(Y)) return nullptr;\n"
                "  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y)); }")
    pair, (status, _) = fn(sound_fn)
    assert status == "proved", ("guarded fold function not proved", status)
    assert {a["name"] for a in pair["assumptions"]} == {"x", "y"}, pair["assumptions"]
    # removing the bailout guard removes the recovered precondition -> the fold is now unsound.
    missing = sound_fn.replace(
        "  if (!isKnownNonNegative(X) || !isKnownNonNegative(Y)) return nullptr;\n", "")
    pair2, (status2, cex2) = fn(missing)
    assert status2 == "refuted" and cex2 and not pair2["assumptions"], \
        ("dropping the guard must remove the precondition and refute", status2, pair2["assumptions"])
    # positive-guard form `if (G) return fold;` recovers the same precondition.
    pos_fn = ("Value *f(BinaryOperator &I){ Value *X,*Y;\n"
              "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
              "  if (isKnownNonNegative(X) && isKnownNonNegative(Y))\n"
              "    return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n"
              "  return nullptr; }")
    assert fn(pos_fn)[1][0] == "proved", "positive-guard fold function not proved"
    # an unmodeled bail guard declines (sound bound).
    assert pg.recover_from_function(sound_fn.replace("isKnownNonNegative(X)", "someUnknownAnalysis(X)")) is None

    # 8) NESTED control flow (phase 1++): the fold inside enclosing positive `if` blocks; the path
    #    condition is the conjunction of the enclosing guards at arbitrary nesting.
    nested_fn = ("Value *f(BinaryOperator &I){ Value *X,*Y;\n"
                 "  if (match(&I, m_SDiv(m_Value(X), m_Value(Y)))) {\n"
                 "    if (isKnownNonNegative(X) && isKnownNonNegative(Y)) {\n"
                 "      return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n"
                 "    }\n"
                 "  }\n"
                 "  return nullptr; }")
    pair, (status, _) = fn(nested_fn)
    assert status == "proved" and {a["name"] for a in pair["assumptions"]} == {"x", "y"}, (status, pair)
    # removing the inner nested guard removes the precondition -> unsound.
    bare = nested_fn.replace("    if (isKnownNonNegative(X) && isKnownNonNegative(Y)) {\n", "    {\n")
    pair2, (status2, cex2) = fn(bare)
    assert status2 == "refuted" and cex2 and not pair2["assumptions"], (status2, pair2["assumptions"])
    # a bailout mixed with nested positive blocks recovers the union of guards.
    mixed_fn = ("Value *f(BinaryOperator &I){ Value *X,*Y;\n"
                "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
                "  if (isKnownNonNegative(X)) {\n"
                "    if (isKnownNonNegative(Y)) {\n"
                "      return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n"
                "    }\n"
                "  }\n"
                "  return nullptr; }")
    assert fn(mixed_fn)[1][0] == "proved", "bailout + nested positive blocks not proved"

    # 9) RECONCILIATION (phase 3): cross-check the recovered obligation across two independent
    #    engines -- the symbolic z3 proof (bv32) and exhaustive CONCRETE enumeration (bv8). A sound
    #    identity holds at every width, so they must AGREE; a divergence flags an untrustworthy fold.
    nested = pg.recover_pair("match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One()))",
                             "return replaceInstUsesWith(I, X);")
    rec = pg.reconcile(nested, z3)
    assert rec["z3"] == "proved" and rec["concrete"] == "proved" and rec["agree"] and rec["checked"] > 0, rec
    # TEETH: a width-NON-uniform obligation ((X & 0xFF) == X holds at bv8 but not bv32) is caught as
    # a DISAGREEMENT -- the reconciliation refuses to trust a fold the two engines don't agree on.
    wnu = {"domain": "scalar-bv32", "marker": "probe.wnu", "variables": ["x"], "equivalence": "result",
           "before": {"op": "bvand", "args": [{"op": "var", "name": "x"}, {"op": "bvconst", "bits": 32, "value": 0xFF}]},
           "after": {"op": "var", "name": "x"}, "assumptions": []}
    rw = pg.reconcile(wnu, z3)
    assert rw["z3"] == "refuted" and rw["concrete"] == "proved" and not rw["agree"], \
        ("width-non-uniform fold must be flagged as a cross-engine disagreement", rw)
    # div/rem are honestly `skipped` (toolless evaluator can't match z3's div-by-zero convention).
    dv = pg.reconcile(pg.recover_pair("match(&I, m_SDiv(m_Value(X), m_Value(Y)))",
                                      "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"), z3)
    assert dv["concrete"] == "skipped", ("div/rem must be conservatively skipped, not falsely (dis)agreed", dv)

    # 10) COMPILED RECONCILIATION (phase 3b): realize the recovered fold as a symbolic_llvm.h harness,
    #     COMPILE it, symbolically execute it through its real branches (real_pass), and require the
    #     compiled-path verdict to match z3 -- an independent compiled oracle. Skipped without clang++.
    clang = shutil.which("clang++") or ("/usr/bin/clang++" if Path("/usr/bin/clang++").exists() else None)
    # the generator emits a compilable harness regardless of clang availability.
    harness = pg.to_shim_harness(pg.recover_pair(
        "match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) && isKnownNonNegative(Y)",
        "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));"))
    assert harness and "isKnownNonNegative(X)" in harness and "B.CreateUDiv" in harness, harness
    if clang is not None:
        guarded = pg.recover_pair(
            "match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) && isKnownNonNegative(Y)",
            "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));")
        rc = pg.reconcile_compiled(guarded, z3, clang=clang)
        assert rc["z3"] == "proved" and rc["compiled"] == "proved" and rc["agree"], ("guarded fold", rc)
        unguarded = pg.recover_pair("match(&I, m_SDiv(m_Value(X), m_Value(Y)))",
                                    "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));")
        ru = pg.reconcile_compiled(unguarded, z3, clang=clang)
        assert ru["z3"] == "refuted" and ru["compiled"] == "refuted" and ru["agree"], ("unguarded fold", ru)
        rn = pg.reconcile_compiled(nested, z3, clang=clang)
        assert rn["compiled"] == "proved" and rn["agree"], ("unconditional nested fold", rn)

    print("pass_graph_fixture OK: compositional recovery proves a NESTED (X+0)*1->X and a "
          "registry-less or-self (X|X->X); a wrong fold is refuted with a witness; unmodeled "
          "matchers decline; and RECOVERED PRECONDITIONS are load-bearing -- sdiv->udiv refutes "
          "unguarded, proves under both-operands-nonneg, refutes on an insufficient guard, and is "
          "caught vacuous on a contradictory one -- structural DFG/CFG recovery gated by the prover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
