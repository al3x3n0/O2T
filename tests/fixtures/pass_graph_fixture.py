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

    # 5) SOUND boundary: an unmodeled matcher is declined (None), never mis-modeled. A type-changing
    #    matcher (m_Trunc) is outside the scalar-bv32 domain, and an intrinsic matcher is unmodeled.
    assert pg.recover_pair("match(&I, m_Trunc(m_Value(X)))",
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

    # 11) INTERPROCEDURAL helpers (phase 4): legality/value in a called helper is inlined before
    #     recovery, retiring the 'blocked helper slice'. Single-return guard/value helpers (incl.
    #     chained) resolve; multi-statement helpers decline (sound).
    def fnh(src, helpers=""):
        pair = pg.recover_from_function(src, helpers_source=helpers)
        assert pair is not None, ("helper recovery declined", src)
        return pair, ma.prove(pair, z3)

    guard_helper = ("static bool bothNonNeg(Value X, Value Y) { return isKnownNonNegative(X) && isKnownNonNegative(Y); }\n"
                    "Value *f(BinaryOperator &I){ Value *X,*Y;\n"
                    "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
                    "  if (!bothNonNeg(X, Y)) return nullptr;\n"
                    "  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y)); }")
    pair, (status, _) = fnh(guard_helper)                                 # guard helper inlined
    assert status == "proved" and {a["name"] for a in pair["assumptions"]} == {"x", "y"}, (status, pair)
    # dropping the helper guard removes the precondition -> unsound.
    _, (status, cex) = fnh(guard_helper.replace("  if (!bothNonNeg(X, Y)) return nullptr;\n", ""))
    assert status == "refuted" and cex, ("dropping the helper guard must refute", status)
    # VALUE helper building the rewrite is inlined.
    value_helper = ("static Value mkUDiv(Value X, Value Y, IRBuilder B) { return B.CreateUDiv(X, Y); }\n" + guard_helper.replace(
        "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));",
        "return replaceInstUsesWith(I, mkUDiv(X, Y, Builder));"))
    assert fnh(value_helper)[1][0] == "proved", "value helper not inlined/proved"
    # CHAINED helper (helper calls helper) resolves recursively.
    chained = ("static bool nn(Value V) { return isKnownNonNegative(V); }\n"
               "static bool bothNN(Value X, Value Y) { return nn(X) && nn(Y); }\n"
               "Value *f(BinaryOperator &I){ Value *X,*Y;\n"
               "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
               "  if (!bothNN(X, Y)) return nullptr;\n"
               "  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y)); }")
    assert fnh(chained)[1][0] == "proved", "chained helper not resolved"
    # a MULTI-statement helper is not inlinable -> the guard is unresolved -> decline (sound).
    multi = ("static bool cx(Value X) { int t = 0; return isKnownNonNegative(X); }\n"
             "Value *f(BinaryOperator &I){ Value *X,*Y;\n"
             "  if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) return nullptr;\n"
             "  if (!cx(X)) return nullptr;\n"
             "  return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y)); }")
    assert pg.recover_from_function(multi) is None, "multi-statement helper must decline"

    # 12) LOOPS OVER IR (phase 5): a `for (Instruction &I : BB)` fold applies the rewrite to every
    #     matching instruction -- an independent per-instruction obligation, so the loop is a
    #     universal quantifier (no value precondition) and the statement-form rewrite under its
    #     guards is recovered. Handles the common `if (!match) continue; ... replaceInstUsesWith(I,X);`.
    loop_fold = ("Value *f(BasicBlock &BB){\n"
                 "  for (Instruction &I : BB) {\n"
                 "    Value *X, *Y;\n"
                 "    if (!match(&I, m_SDiv(m_Value(X), m_Value(Y)))) continue;\n"
                 "    if (!isKnownNonNegative(X) || !isKnownNonNegative(Y)) continue;\n"
                 "    replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));\n"
                 "  }\n"
                 "  return nullptr; }")
    pair, (status, _) = fn(loop_fold)
    assert status == "proved" and {a["name"] for a in pair["assumptions"]} == {"x", "y"}, (status, pair)
    # dropping the loop-body guard removes the precondition -> unsound.
    _, (status, cex) = fn(loop_fold.replace(
        "    if (!isKnownNonNegative(X) || !isKnownNonNegative(Y)) continue;\n", ""))
    assert status == "refuted" and cex, ("loop fold without guard must refute", status)
    # statement-form rewrite guarded inline in the loop `if (match && guards) replaceInstUsesWith(...)`.
    inline_loop = ("Value *f(BasicBlock &BB){\n"
                   "  for (Instruction &I : BB) {\n"
                   "    Value *X;\n"
                   "    if (match(&I, m_Mul(m_Add(m_Value(X), m_Zero()), m_One())))\n"
                   "      replaceInstUsesWith(I, X);\n"
                   "  }\n"
                   "  return nullptr; }")
    assert fn(inline_loop)[1][0] == "proved", "loop + nested-identity statement rewrite not proved"

    # 13) RELATIONAL PRECONDITIONS (phase 6): a guard relating TWO bound operands -- disjointness
    #     `haveNoCommonBitsSet(X, Y)` / `MaskedValueIsZero`. `add X,Y -> or X,Y` is UNSOUND in general
    #     but sound when the operands share no set bits. This closes a prover-drift gap: the two-operand
    #     `mask-pair` fact was lowered by the symexec guard path but NOT by the formal-IR prover.
    add = "match(&I, m_Add(m_Value(X), m_Value(Y)))"
    to_or = "return replaceInstUsesWith(I, Builder.CreateOr(X, Y));"
    _, (status, cex) = prove(add, to_or)                                  # no guard -> unsound
    assert status == "refuted" and cex, ("unguarded add->or must refute", status)
    pair, (status, _) = prove(add + " && haveNoCommonBitsSet(X, Y)", to_or)
    assert status == "proved", ("disjoint add->or must prove", status)
    assert pair["assumptions"] == [{"op": "mask-pair", "left": "x", "right": "y"}], pair["assumptions"]
    # the two-operand precondition is load-bearing on BOTH operands: a guard naming an UNBOUND value
    # (never matched) must decline, never silently drop the premise.
    assert pg.recover_pair(add + " && haveNoCommonBitsSet(X, Z)", to_or) is None
    # reconciliation: the disjointness-guarded fold agrees across the z3 and concrete engines.
    rec = pg.reconcile(pair, z3)
    assert rec["z3"] == "proved" and rec["concrete"] == "proved" and rec["agree"] and rec["checked"] > 0, rec
    # function form: a loop with a De Morgan'd `if (!haveNoCommonBitsSet(X,Y)) continue;` bail recovers
    # the same relational precondition and proves under it.
    disjoint_fn = ("Value *f(BasicBlock &BB){\n"
                   "  for (Instruction &I : BB) {\n"
                   "    Value *X, *Y;\n"
                   "    if (!match(&I, m_Add(m_Value(X), m_Value(Y)))) continue;\n"
                   "    if (!haveNoCommonBitsSet(X, Y)) continue;\n"
                   "    replaceInstUsesWith(I, Builder.CreateOr(X, Y));\n"
                   "  }\n"
                   "  return nullptr; }")
    pair, (status, _) = fn(disjoint_fn)
    assert status == "proved" and pair["assumptions"] == [{"op": "mask-pair", "left": "x", "right": "y"}], (status, pair)
    # dropping the disjointness guard removes the precondition -> unsound.
    _, (status, cex) = fn(disjoint_fn.replace("    if (!haveNoCommonBitsSet(X, Y)) continue;\n", ""))
    assert status == "refuted" and cex, ("add->or without disjointness must refute", status)

    # 14) SELECT FOLDS (phase 7): `m_Select`/`CreateSelect` lower to an ite node whose condition is
    #     coerced to a boolean (`C != 0`, LLVM i1 semantics), so select folds prove in the shared
    #     scalar domain and compose with the existing matcher/rewrite algebra.
    #     arm-equal `select C, X, X -> X` is sound for ANY condition.
    pair, (status, _) = prove("match(&I, m_Select(m_Value(C), m_Value(X), m_Deferred(X)))",
                              "return replaceInstUsesWith(I, X);")
    assert status == "proved" and pair["before"]["op"] == "ite", (status, pair["before"])
    # TEETH: `select C, X, Y -> X` is UNSOUND (the else arm leaks when C is false) -> refuted.
    _, (status, cex) = prove("match(&I, m_Select(m_Value(C), m_Value(X), m_Value(Y)))",
                             "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("unguarded select arm-pick must refute", status)
    # CONSTANT condition selects the corresponding arm; the wrong arm refutes.
    assert prove("match(&I, m_Select(m_One(), m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "select true -> then"
    assert prove("match(&I, m_Select(m_Zero(), m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Y);")[1][0] == "proved", "select false -> else"
    _, (status, _) = prove("match(&I, m_Select(m_One(), m_Value(X), m_Value(Y)))",
                           "return replaceInstUsesWith(I, Y);")
    assert status == "refuted", ("select true must not fold to the else arm", status)
    # COMPOSITIONAL: an arm is itself a nested identity `(X+0)`, equal to the other arm -> proves.
    assert prove("match(&I, m_Select(m_Value(C), m_Add(m_Value(X), m_Zero()), m_Deferred(X)))",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "select over nested identity arm"
    # a `CreateSelect(C, X, Y)` rewrite reconstructing the same select is an identity.
    assert prove("match(&I, m_Select(m_Value(C), m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateSelect(C, X, Y));")[1][0] == "proved", \
        "CreateSelect passthrough"
    # concrete reconciliation agrees on a select fold (the ite is handled by the concrete evaluator).
    rec = pg.reconcile(pair, z3)
    assert rec["z3"] == "proved" and rec["concrete"] == "proved" and rec["agree"], rec
    # the compiled-shim path has no select builder -> declines cleanly (None), never a bogus verdict.
    assert pg.to_shim_harness(pair) is None, "select has no shim mapping -> harness must decline"
    # FUNCTION form: arm-equal select recovered from a fold function's control flow proves.
    sel_fn = ("Value *f(SelectInst &I){ Value *C, *X;\n"
              "  if (!match(&I, m_Select(m_Value(C), m_Value(X), m_Deferred(X)))) return nullptr;\n"
              "  return replaceInstUsesWith(I, X); }")
    assert fn(sel_fn)[1][0] == "proved", "function-form arm-equal select not proved"
    # SOUND boundary: an unmodeled condition matcher (m_ICmp) inside the select declines, never mis-models.
    assert pg.recover_pair("match(&I, m_Select(m_ICmp(P, m_Value(A), m_Value(B)), m_Value(X), m_Value(Y)))",
                           "return replaceInstUsesWith(I, X);") is None

    # 15) WIDTH-CHANGING CASTS (phase 8): `m_Trunc`/`m_ZExt`/`m_SExt` lower to mixed-width cast nodes
    #     at representative widths (narrow 8 <-> wide 32). Because the matcher tree carries no bit
    #     widths, a cast round-trip is recovered ONLY when licensed by an explicit width-equality guard
    #     `X->getType() == I.getType()` (the fold `replaceInstUsesWith(I, X)` is well-typed only then),
    #     so a width-dependent fold can never become a false proof.
    te = " && X->getType() == I.getType()"
    trunc_zext = "match(&I, m_Trunc(m_ZExt(m_Value(X))))"
    pair, (status, _) = prove(trunc_zext + te, "return replaceInstUsesWith(I, X);")
    assert status == "proved" and pair["variable_bits"] == {"x": 8}, (status, pair.get("variable_bits"))
    assert pair["before"]["op"] == "trunc" and pair["before"]["args"][0]["op"] == "zext", pair["before"]
    # trunc(sext(X)) -> X is the same round-trip identity.
    assert prove("match(&I, m_Trunc(m_SExt(m_Value(X))))" + te,
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "trunc(sext(X))->X"
    # SOUND GATE: without the width-equality guard the representative widths are unlicensed -> decline.
    assert pg.recover_pair(trunc_zext, "return replaceInstUsesWith(I, X);") is None, \
        "cast fold without a type-equality guard must decline"
    # TEETH: `zext(trunc(X)) -> X` masks the high bits -- UNSOUND even with the guard -> refuted.
    _, (status, cex) = prove("match(&I, m_ZExt(m_Trunc(m_Value(X))))" + te,
                             "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("zext(trunc(X))->X must refute", status)
    # the concrete + compiled engines cannot evaluate a width-changing cast, so they honestly abstain
    # (skip/decline) rather than emit a bogus verdict; z3 remains authoritative.
    assert pg.reconcile(pair, z3)["concrete"] == "skipped", "cast reconcile must be conservatively skipped"
    assert pg.to_shim_harness(pair) is None, "cast has no shim builder -> harness declines"
    # FUNCTION form: a positive `if (X->getType() == I.getType()) return fold;` guard licenses recovery.
    cast_fn = ("Value *f(Instruction &I){ Value *X;\n"
               "  if (!match(&I, m_Trunc(m_ZExt(m_Value(X))))) return nullptr;\n"
               "  if (X->getType() == I.getType())\n"
               "    return replaceInstUsesWith(I, X);\n"
               "  return nullptr; }")
    pair, (status, _) = fn(cast_fn)
    assert status == "proved" and pair["variable_bits"] == {"x": 8}, (status, pair.get("variable_bits"))
    # CROSS-WIDTH RECONCILIATION (phase 16): a cast is proved at ONE representative (narrow, wide) pair
    # and no other engine can evaluate a width change, so re-prove at several pairs. A width-uniform
    # identity holds at every pair (verdicts AGREE); a width-specific coincidence would diverge.
    tz = pg.recover_pair("match(&I, m_Trunc(m_ZExt(m_Value(X))))" + te, "return replaceInstUsesWith(I, X);")
    rw = pg.reconcile_widths(tz, z3)
    assert rw["applicable"] and rw["agree"] and rw["status"] == "proved", rw
    assert set(rw["verdicts"]) == {(8, 32), (4, 16), (16, 32)}, rw["verdicts"]
    # the unsound zext(trunc(X))->X is consistently refuted at every width -- also an agreement.
    zt = pg.recover_pair("match(&I, m_ZExt(m_Trunc(m_Value(X))))" + te, "return replaceInstUsesWith(I, X);")
    assert pg.reconcile_widths(zt, z3)["status"] == "refuted", "unsound cast must refute at every width"
    # a non-cast fold has nothing width-parametric to cross-check.
    assert pg.reconcile_widths(
        pg.recover_pair("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
        z3) == {"applicable": False}
    # TEETH on the cross-check itself: a width-NON-uniform cast obligation (zext(X) vs zext(X) & 0xFF,
    # true only when X fits in 8 bits) is caught as a DISAGREEMENT -- what a single-width proof misses.
    ze = {"op": "zext", "bits": 32, "args": [{"op": "var", "name": "x"}]}
    wnu = {"domain": "scalar-bv32", "marker": "probe.wnu.cast", "variables": ["x"], "equivalence": "result",
           "variable_bits": {"x": 8}, "before": ze,
           "after": {"op": "bvand", "args": [ze, {"op": "bvconst", "bits": 32, "value": 0xFF}]},
           "assumptions": []}
    rwnu = pg.reconcile_widths(wnu, z3)
    assert not rwnu["agree"] and rwnu["status"] == "disagree", \
        ("a width-non-uniform cast fold must be flagged as a cross-width disagreement", rwnu)

    # 16) ICMP PREDICATE MATCHERS (phase 9): `m_SpecificICmp(PRED, ...)` (literal predicate) and
    #     `m_ICmp(Pred, ...)` fixed by a `Pred == ICmpInst::ICMP_*` guard lower to a 0/1 bitvector
    #     `pred(a,b) ? 1 : 0`, so icmp folds stay in the shared domain and reconcile concretely.
    eqxx = "match(&I, m_SpecificICmp(ICmpInst::ICMP_EQ, m_Value(X), m_Deferred(X)))"
    pair, (status, _) = prove(eqxx, "return replaceInstUsesWith(I, getTrue());")
    assert status == "proved" and pair["before"]["op"] == "ite", (status, pair["before"])
    # m_ICmp with a bound predicate fixed by a guard: `icmp ne X, X -> false`.
    assert prove("match(&I, m_ICmp(Pred, m_Value(X), m_Deferred(X))) && Pred == ICmpInst::ICMP_NE",
                 "return replaceInstUsesWith(I, getFalse());")[1][0] == "proved", "icmp ne X,X -> false"
    # a constant operand: `icmp ult X, 0 -> false` (nothing is unsigned-less-than zero).
    assert prove("match(&I, m_SpecificICmp(ICmpInst::ICMP_ULT, m_Value(X), m_Zero()))",
                 "return replaceInstUsesWith(I, ConstantInt::getFalse());")[1][0] == "proved", "ult X,0 -> false"
    # TEETH: `icmp eq X, X -> false` is wrong -> refuted with a witness.
    _, (status, cex) = prove(eqxx, "return replaceInstUsesWith(I, getFalse());")
    assert status == "refuted" and cex, ("icmp eq X,X -> false must refute", status)
    # unsound `icmp eq A, B -> true` (distinct operands) refutes.
    _, (status, _) = prove("match(&I, m_ICmp(Pred, m_Value(A), m_Value(B))) && Pred == ICmpInst::ICMP_EQ",
                           "return replaceInstUsesWith(I, getTrue());")
    assert status == "refuted", ("icmp eq A,B -> true must refute", status)
    # SOUND boundary: an m_ICmp whose predicate is neither literal nor guard-fixed declines.
    assert pg.recover_pair("match(&I, m_ICmp(Pred, m_Value(A), m_Deferred(A)))",
                           "return replaceInstUsesWith(I, getTrue());") is None
    # a `CreateICmpEQ(A, B)` rewrite reconstructing the same compare is an identity.
    assert prove("match(&I, m_SpecificICmp(ICmpInst::ICMP_EQ, m_Value(A), m_Value(B)))",
                 "return replaceInstUsesWith(I, Builder.CreateICmpEQ(A, B));")[1][0] == "proved", \
        "CreateICmp passthrough"
    # unlike casts, an icmp IS concretely evaluable -> the concrete engine reconciles (cross-engine teeth).
    rec = pg.reconcile(pair, z3)
    assert rec["z3"] == "proved" and rec["concrete"] == "proved" and rec["agree"], rec
    # COMPOSITION with phase 7: `select(icmp eq X,X, A, B) -> A` (the condition is always true).
    assert prove("match(&I, m_Select(m_SpecificICmp(ICmpInst::ICMP_EQ, m_Value(X), m_Deferred(X)), "
                 "m_Value(A), m_Value(B)))", "return replaceInstUsesWith(I, A);")[1][0] == "proved", \
        "select over an always-true icmp -> then arm"
    # FUNCTION form: m_ICmp bound predicate fixed by a positive `if (Pred == ICMP_UGE)` guard.
    icmp_fn = ("Value *f(ICmpInst &I){ Value *X; ICmpInst::Predicate Pred;\n"
               "  if (!match(&I, m_ICmp(Pred, m_Value(X), m_Deferred(X)))) return nullptr;\n"
               "  if (Pred == ICmpInst::ICMP_UGE)\n"
               "    return replaceInstUsesWith(I, getTrue());\n"
               "  return nullptr; }")
    assert fn(icmp_fn)[1][0] == "proved", "function-form icmp uge X,X -> true not proved"

    # 17) MIN/MAX INTRINSICS (phase 10): `m_SMin/SMax/UMin/UMax` and `CreateBinaryIntrinsic(Intrinsic::
    #     smin, ...)` / `CreateSMin(...)` model each as `keep-x-when(x,y) ? x : y`. Because that is the
    #     same ite algebra as select+icmp (phases 7,9), a min-select CANONICALIZES into the intrinsic
    #     and proves by construction -- the payoff of ordering these phases together.
    min_sel = ("match(&I, m_Select(m_SpecificICmp(ICmpInst::ICMP_SLT, m_Value(X), m_Value(Y)), "
               "m_Value(X), m_Value(Y)))")
    pair, (status, _) = prove(min_sel, "return replaceInstUsesWith(I, Builder.CreateBinaryIntrinsic(Intrinsic::smin, X, Y));")
    assert status == "proved", ("min-select -> smin not proved", status)
    # swapped arms canonicalize to smax; an unsigned compare canonicalizes to umax.
    assert prove("match(&I, m_Select(m_SpecificICmp(ICmpInst::ICMP_SLT, m_Value(X), m_Value(Y)), "
                 "m_Value(Y), m_Value(X)))",
                 "return replaceInstUsesWith(I, Builder.CreateSMax(X, Y));")[1][0] == "proved", "max-select -> smax"
    assert prove("match(&I, m_Select(m_SpecificICmp(ICmpInst::ICMP_UGT, m_Value(X), m_Value(Y)), "
                 "m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateUMax(X, Y));")[1][0] == "proved", "umax-select -> umax"
    # TEETH: a min-select canonicalized to the WRONG intrinsic (smax) refutes with a witness.
    _, (status, cex) = prove(min_sel, "return replaceInstUsesWith(I, Builder.CreateBinaryIntrinsic(Intrinsic::smax, X, Y));")
    assert status == "refuted" and cex, ("min-select -> smax must refute", status)
    # MATCHER form: `smin(X, X) -> X` is idempotent; and a min/max round-trips back to its select.
    assert prove("match(&I, m_SMin(m_Value(X), m_Deferred(X)))",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "smin(X,X) -> X"
    assert prove("match(&I, m_SMin(m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateSelect(Builder.CreateICmpSLT(X, Y), X, Y));")[1][0] \
        == "proved", "smin(X,Y) -> equivalent select"
    # a min/max intrinsic is concretely evaluable -> all engines reconcile.
    rec = pg.reconcile(pair, z3)
    assert rec["z3"] == "proved" and rec["concrete"] == "proved" and rec["agree"], rec
    # SOUND boundary: a non-min/max binary intrinsic has no model -> declines.
    assert pg.recover_pair("match(&I, m_Value(X))",
                           "return replaceInstUsesWith(I, Builder.CreateBinaryIntrinsic(Intrinsic::bswap, X, X));") is None

    # 18) POISON / FREEZE (phase 11): `m_Freeze`/`CreateFreeze` lower to a freeze node over a
    #     poison-declared value, and `isGuaranteedNotToBeUndefOrPoison(X)` is recovered as a
    #     `not-poison` precondition (not dropped). `freeze(X) -> X` is UNSOUND in general (X may be
    #     poison) but sound when X is guaranteed non-poison -- the guard is load-bearing.
    freeze_x = "match(&I, m_Freeze(m_Value(X)))"
    pair, (status, _) = prove(freeze_x + " && isGuaranteedNotToBeUndefOrPoison(X)",
                              "return replaceInstUsesWith(I, X);")
    assert status == "proved", ("guarded freeze(X)->X not proved", status)
    assert pair["poison_variables"] == ["x"] and pair["assumptions"] == [{"op": "not-poison", "name": "x"}], pair
    # TEETH: without the poison-freedom guard, X may be poison -> refuted (freeze cannot be dropped).
    _, (status, cex) = prove(freeze_x, "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("unguarded freeze(X)->X must refute", status)
    # the value under freeze is declared poison even without a guard -- that is what gives the teeth.
    unguarded = pg.recover_pair(freeze_x, "return replaceInstUsesWith(I, X);")
    assert unguarded["poison_variables"] == ["x"] and not unguarded["assumptions"], unguarded
    # a poison-freedom guard on an UNBOUND value (never matched) declines, never silently dropped.
    assert pg.recover_pair(freeze_x + " && isGuaranteedNotToBeUndefOrPoison(Y)",
                           "return replaceInstUsesWith(I, X);") is None
    # a value-only fold additionally guarded non-poison still proves (poison propagates equally).
    assert prove("match(&I, m_Add(m_Value(X), m_Zero())) && isGuaranteedNotToBePoison(X)",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "add X,0 -> X under not-poison"
    # freeze folds abstain in the toolless engines (freeze is not concretely evaluable) -> z3 authoritative.
    assert pg.reconcile(pair, z3)["concrete"] == "skipped" and pg.to_shim_harness(pair) is None
    # FUNCTION form: a `if (!isGuaranteedNotToBeUndefOrPoison(X)) return nullptr;` bailout recovers the
    # not-poison precondition; dropping it flips the fold to refuted.
    freeze_fn = ("Value *f(FreezeInst &I){ Value *X;\n"
                 "  if (!match(&I, m_Freeze(m_Value(X)))) return nullptr;\n"
                 "  if (!isGuaranteedNotToBeUndefOrPoison(X)) return nullptr;\n"
                 "  return replaceInstUsesWith(I, X); }")
    pair, (status, _) = fn(freeze_fn)
    assert status == "proved" and pair["poison_variables"] == ["x"], (status, pair.get("poison_variables"))
    _, (status, cex) = fn(freeze_fn.replace(
        "  if (!isGuaranteedNotToBeUndefOrPoison(X)) return nullptr;\n", ""))
    assert status == "refuted" and cex, ("dropping the poison guard must refute", status)
    # TWO-LEVEL LATTICE (phase 18): poison and undef are distinct (Lee et al. PLDI'17), and LLVM has
    # two freedom guards. `isGuaranteedNotToBeUndefOrPoison(X)` = X is DEFINITE (licenses freeze drop);
    # `isGuaranteedNotToBePoison(X)` rules out poison ONLY -- X may be undef, and `freeze` exists to
    # collapse undef's use-multiplicity, so a poison-only guard must NOT license dropping a freeze.
    assert prove("match(&I, m_Freeze(m_Value(X))) && isGuaranteedNotToBeUndefOrPoison(X)",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "definite guard licenses freeze-drop"
    # the poison-ONLY guard would falsely prove in a single-poison-bit model (no undef) -> must decline.
    assert pg.recover_pair("match(&I, m_Freeze(m_Value(X))) && isGuaranteedNotToBePoison(X)",
                           "return replaceInstUsesWith(I, X);") is None, \
        "poison-only guard cannot license dropping a freeze (undef still possible)"
    # the distinction only gates freeze REMOVAL: a poison-only guard still discharges a non-freeze fold,
    # and introducing a freeze needs no guard at all.
    assert prove("match(&I, m_Add(m_Value(X), m_Zero())) && isGuaranteedNotToBePoison(X)",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "poison-only ok for non-freeze fold"

    # 19) REFINEMENT-MODE EQUIVALENCE (phase 12): refinement -- not value-equality -- is the true
    #     soundness criterion for `before -> after` (any behaviour of `after` must be allowed for
    #     `before`). It coincides with equality on poison-free folds, but a poison-relevant rewrite may
    #     legitimately be MORE defined. INTRODUCING a `freeze` is always sound, yet value-UNEQUAL, so it
    #     is provable only as a refinement.
    pair, (status, _) = prove("match(&I, m_Value(X))",
                              "return replaceInstUsesWith(I, Builder.CreateFreeze(X));")
    assert status == "proved" and pair.get("refinement") == "refinement", (status, pair.get("refinement"))
    # freezing a computed value is likewise a sound refinement.
    assert prove("match(&I, m_Add(m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateFreeze(Builder.CreateAdd(X, Y)));")[1][0] \
        == "proved", "add(X,Y) -> freeze(add(X,Y)) refinement"
    # TEETH hold under refinement: a value-WRONG freeze rewrite still refutes.
    _, (status, cex) = prove("match(&I, m_Value(X))",
                             "return replaceInstUsesWith(I, Builder.CreateFreeze(Builder.CreateAdd(X, 1)));")
    assert status == "refuted" and cex, ("wrong-value refinement must refute", status)
    # a poison-FREE fold carries no refinement flag and is discharged by value-equality, unchanged.
    plain = pg.recover_pair("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);")
    assert "refinement" not in plain and "poison_variables" not in plain, plain
    # dropping a freeze (the reverse direction) is NOT a refinement unless the value is non-poison, so
    # `freeze(X) -> X` still refutes unguarded and proves under the guard (phase 11 semantics preserved).
    assert prove("match(&I, m_Freeze(m_Value(X)))", "return replaceInstUsesWith(I, X);")[1][0] == "refuted", \
        "unguarded freeze-drop must refute under refinement too"
    assert prove("match(&I, m_Freeze(m_Value(X))) && isGuaranteedNotToBeUndefOrPoison(X)",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "guarded freeze-drop proves"

    # 20) NO-WRAP FLAG MODELING (phase 13): `m_NSWAdd`/`m_NUWMul`/... and `CreateNSWAdd`/... carry a
    #     no-wrap flag whose violation makes the result poison. DROPPING a flag is a sound refinement
    #     (fewer poison inputs); ADDING one is unsound. Discharged via phase 12's refinement check.
    pair, (status, _) = prove("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",
                              "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));")
    assert status == "proved" and pair.get("refinement") == "refinement", (status, pair.get("refinement"))
    assert pair["before"]["flags"] == ["nsw"] and "flags" not in pair["after"], pair["before"]
    # nuw on a multiply drops just the same.
    assert prove("match(&I, m_NUWMul(m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateMul(X, Y));")[1][0] == "proved", "nuw mul drop"
    # TEETH: ADDING a no-wrap flag introduces poison the source lacked -> refuted.
    _, (status, cex) = prove("match(&I, m_Add(m_Value(X), m_Value(Y)))",
                             "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));")
    assert status == "refuted" and cex, ("adding nsw must refute", status)
    # keeping the flag (identity) proves.
    assert prove("match(&I, m_NSWSub(m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateNSWSub(X, Y));")[1][0] == "proved", "nsw sub identity"
    # the toolless/compiled engines model neither poison nor flags -> they abstain; z3 authoritative.
    assert pg.reconcile(pair, z3)["concrete"] == "skipped" and pg.to_shim_harness(pair) is None
    # FUNCTION form: a flag-dropping fold recovered from control flow proves as a refinement.
    flag_fn = ("Value *f(BinaryOperator &I){ Value *X, *Y;\n"
               "  if (!match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))) return nullptr;\n"
               "  return replaceInstUsesWith(I, Builder.CreateAdd(X, Y)); }")
    assert fn(flag_fn)[1][0] == "proved", "function-form nsw drop not proved"
    # EXACT FLAG (phase 21): `exact` on lshr/ashr is poison when a shifted-out bit is nonzero. Like
    # nsw/nuw, dropping it is a sound refinement and adding it is not. `m_Exact(SUB)` is a wrapper.
    exact = prove("match(&I, m_Exact(m_LShr(m_Value(X), m_Value(Y))))",
                  "return replaceInstUsesWith(I, Builder.CreateLShr(X, Y));")
    assert exact[1][0] == "proved" and exact[0]["before"]["flags"] == ["exact"], exact[0]["before"]
    assert prove("match(&I, m_Exact(m_AShr(m_Value(X), m_Value(Y))))",
                 "return replaceInstUsesWith(I, Builder.CreateAShr(X, Y));")[1][0] == "proved", "exact ashr drop"
    # ADDING exact introduces poison the source lacked -> refuted with a witness.
    _, (status, cex) = prove("match(&I, m_LShr(m_Value(X), m_Value(Y)))",
                             "return replaceInstUsesWith(I, Builder.CreateExactLShr(X, Y));")
    assert status == "refuted" and cex, ("adding exact must refute", status)
    # exact is a shift-only flag: m_Exact over a non-shift declines (sound boundary).
    assert pg.recover_pair("match(&I, m_Exact(m_UDiv(m_Value(X), m_Value(Y))))",
                           "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));") is None
    # DISJOINT FLAG (phase 22): `or disjoint X, Y` is poison when the operands share a set bit. Dropping
    # it is a refinement; adding it is unsound; and because disjoint operands add without carry,
    # `or disjoint X, Y` refines to `add X, Y` -- a semantically load-bearing cross-op fold.
    dj = prove("match(&I, m_DisjointOr(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, Builder.CreateOr(X, Y));")
    assert dj[1][0] == "proved" and dj[0]["before"]["flags"] == ["disjoint"], dj[0]["before"]
    assert prove("match(&I, m_DisjointOr(m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));")[1][0] == "proved", "or disjoint -> add"
    _, (status, cex) = prove("match(&I, m_Or(m_Value(X), m_Value(Y)))",
                             "return replaceInstUsesWith(I, Builder.CreateDisjointOr(X, Y));")
    assert status == "refuted" and cex, ("adding disjoint must refute", status)
    # POISON/FLAG CROSS-CHECK (phase 17): refinement folds (poison/freeze/flags) abstain from the
    # value-equality + compiled engines, so they would trust z3 alone. An INDEPENDENT poison/flag-aware
    # concrete oracle re-checks the actual refinement condition and must AGREE with z3 on each.
    refinement_folds = [
        ("match(&I, m_NSWAdd(m_Value(X), m_Value(Y)))",                       # flag-drop: proved
         "return replaceInstUsesWith(I, Builder.CreateAdd(X, Y));", "proved"),
        ("match(&I, m_Add(m_Value(X), m_Value(Y)))",                          # add a flag: refuted
         "return replaceInstUsesWith(I, Builder.CreateNSWAdd(X, Y));", "refuted"),
        ("match(&I, m_Value(X))",                                             # introduce freeze: proved
         "return replaceInstUsesWith(I, Builder.CreateFreeze(X));", "proved"),
        ("match(&I, m_Freeze(m_Value(X))) && isGuaranteedNotToBeUndefOrPoison(X)",  # guarded drop: proved
         "return replaceInstUsesWith(I, X);", "proved"),
        ("match(&I, m_Freeze(m_Value(X)))",                                   # unguarded drop: refuted
         "return replaceInstUsesWith(I, X);", "refuted"),
    ]
    for pred, rw, expect in refinement_folds:
        pair = pg.recover_pair(pred, rw)
        assert pair.get("refinement") == "refinement", ("expected a refinement fold", pred)
        rc = pg.reconcile_refinement(pair, z3)
        assert rc["z3"] == expect and rc["concrete"] == expect and rc["agree"], (pred, rc)
    # the oracle abstains (honestly) on a NON-refinement fold -- z3 already has the value-equality engine.
    assert pg.reconcile_refinement(
        pg.recover_pair("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X);"),
        z3)["concrete"] == "skipped", "non-refinement fold is not this oracle's job"

    # 21) GENERIC INTRINSIC MATCHER (phase 14): the `m_Intrinsic<Intrinsic::ID>(...)` template form is
    #     parsed (the tokenizer/parser now carry the `<...>` template id) and dispatched to a model.
    #     min/max route to the phase-10 semantics; abs is a new unary intrinsic `x <s 0 ? -x : x`.
    assert prove("match(&I, m_Intrinsic<Intrinsic::smin>(m_Value(X), m_Deferred(X)))",
                 "return replaceInstUsesWith(I, X);")[1][0] == "proved", "generic smin(X,X) -> X"
    # a min-select canonicalizes into the generic intrinsic builder just as with CreateSMin.
    assert prove("match(&I, m_Select(m_SpecificICmp(ICmpInst::ICMP_SLT, m_Value(X), m_Value(Y)), "
                 "m_Value(X), m_Value(Y)))",
                 "return replaceInstUsesWith(I, Builder.CreateBinaryIntrinsic(Intrinsic::smin, X, Y));")[1][0] \
        == "proved", "min-select -> generic smin intrinsic"
    # ABS: `abs(abs(X)) -> abs(X)` is idempotent (the int-min-poison flag is conservatively ignored).
    pair, (status, _) = prove(
        "match(&I, m_Intrinsic<Intrinsic::abs>(m_Intrinsic<Intrinsic::abs>(m_Value(X), m_Zero()), m_Zero()))",
        "return replaceInstUsesWith(I, Builder.CreateBinaryIntrinsic(Intrinsic::abs, X, false));")
    assert status == "proved" and pair["before"]["op"] == "ite", (status, pair["before"])
    # TEETH: `abs(X) -> X` is wrong (negative inputs) -> refuted with a witness.
    _, (status, cex) = prove("match(&I, m_Intrinsic<Intrinsic::abs>(m_Value(X), m_Zero()))",
                             "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("abs(X) -> X must refute", status)
    # SOUND boundary: an intrinsic with no model (ctpop) parses now but still declines semantically.
    assert pg.recover_pair("match(&I, m_Intrinsic<Intrinsic::ctpop>(m_Value(X)))",
                           "return replaceInstUsesWith(I, X);") is None
    # BSWAP (phase 23): `@llvm.bswap.i32` is modeled EXACTLY in existing ops (mask/shift/or) at the
    # domain width, so every engine handles it. Byte-swap is an involution -> bswap(bswap(X)) proves;
    # bswap(X) -> X is wrong. The `CreateUnaryIntrinsic(Intrinsic::bswap, X)` builder round-trips.
    bs = "m_Intrinsic<Intrinsic::bswap>"
    inv = prove(f"match(&I, {bs}({bs}(m_Value(X))))", "return replaceInstUsesWith(I, X);")
    assert inv[1][0] == "proved" and inv[0]["before"]["op"] == "bvor", ("bswap involution", inv[1][0])
    _, (status, cex) = prove(f"match(&I, {bs}(m_Value(X)))", "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("bswap(X) -> X must refute", status)
    assert prove(f"match(&I, {bs}(m_Value(X)))",
                 "return replaceInstUsesWith(I, Builder.CreateUnaryIntrinsic(Intrinsic::bswap, X));")[1][0] \
        == "proved", "bswap builder round-trip"
    # bswap is bv32-specific, so the meaningful independent check runs the REAL IR at i32 through clang;
    # it agrees with z3 (involution proves, and the wrong fold is a concrete mismatch).
    if shutil.which("clang"):
        p_inv = pg.recover_pair(f"match(&I, {bs}({bs}(m_Value(X))))", "return replaceInstUsesWith(I, X);")
        assert pg.reconcile_vellvm(p_inv, z3)["agree"], "clang must confirm the bswap involution at i32"
        p_wrong = pg.recover_pair(f"match(&I, {bs}(m_Value(X)))", "return replaceInstUsesWith(I, X);")
        rc = pg.reconcile_vellvm(p_wrong, z3)
        assert rc["interp"] == "refuted" and rc["agree"], ("clang must catch bswap(X)->X", rc)
    # BITREVERSE (phase 24): `@llvm.bitreverse.i32` as the 5-step parallel bit-reversal network, again
    # in existing ops. Also an involution; and it is DISTINCT from bswap, so bitreverse -> bswap refutes.
    br = "m_Intrinsic<Intrinsic::bitreverse>"
    inv = prove(f"match(&I, {br}({br}(m_Value(X))))", "return replaceInstUsesWith(I, X);")
    assert inv[1][0] == "proved", ("bitreverse involution", inv[1][0])
    assert prove(f"match(&I, {br}(m_Value(X)))",
                 "return replaceInstUsesWith(I, Builder.CreateUnaryIntrinsic(Intrinsic::bitreverse, X));")[1][0] \
        == "proved", "bitreverse builder round-trip"
    _, (status, cex) = prove(f"match(&I, {br}(m_Value(X)))", "return replaceInstUsesWith(I, X);")
    assert status == "refuted" and cex, ("bitreverse(X) -> X must refute", status)
    # bitreverse and bswap are different permutations -> conflating them refutes with a witness.
    _, (status, cex) = prove(f"match(&I, {br}(m_Value(X)))",
                             "return replaceInstUsesWith(I, Builder.CreateUnaryIntrinsic(Intrinsic::bswap, X));")
    assert status == "refuted" and cex, ("bitreverse is not bswap", status)
    # FUNNEL SHIFT (phase 25): `@llvm.fshl/fshr(A, B, C)` -- concat A:B, shift by C mod 32, take the
    # top/bottom 32 bits -- in existing shift/or ops, with the `C mod 32 == 0` case an explicit branch
    # so z3 and the masking concrete evaluator agree. Shift by 0 selects the funnel's leading operand.
    fl = "m_Intrinsic<Intrinsic::fshl>"
    fr = "m_Intrinsic<Intrinsic::fshr>"
    assert prove(f"match(&I, {fl}(m_Value(A), m_Value(B), m_Zero()))",
                 "return replaceInstUsesWith(I, A);")[1][0] == "proved", "fshl(A,B,0) -> A"
    assert prove(f"match(&I, {fr}(m_Value(A), m_Value(B), m_Zero()))",
                 "return replaceInstUsesWith(I, B);")[1][0] == "proved", "fshr(A,B,0) -> B"
    # a non-zero (symbolic) shift is NOT the identity -> refuted with a witness.
    _, (status, cex) = prove(f"match(&I, {fl}(m_Value(A), m_Value(B), m_Value(C)))",
                             "return replaceInstUsesWith(I, A);")
    assert status == "refuted" and cex, ("fshl(A,B,C) -> A must refute for symbolic C", status)
    if shutil.which("clang"):
        pf = pg.recover_pair(f"match(&I, {fl}(m_Value(A), m_Value(B), m_Zero()))",
                             "return replaceInstUsesWith(I, A);")
        assert pg.reconcile_vellvm(pf, z3)["agree"], "clang must confirm fshl(A,B,0)->A at i32"

    # 22) PARSER SOUNDNESS: the tokenizer must REJECT any operator it does not model and the parser
    #     must consume every token, so an infix/ternary rewrite can never SILENTLY misparse to a
    #     prefix and prove a model the source never expressed. Each of these declines (None).
    misparse_cases = [
        # `X & Y` would drop the operator and misparse to `X` -- a WRONG-but-provable model for or-self.
        ("match(&I, m_Or(m_Value(X), m_Deferred(X)))", "return replaceInstUsesWith(I, X & Y);"),
        ("match(&I, m_Add(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X + Y);"),
        ("match(&I, m_Sub(m_Value(X), m_Value(Y)))", "return replaceInstUsesWith(I, X - Y);"),
        ("match(&I, m_Value(X))", "return replaceInstUsesWith(I, C ? X : Y);"),
        ("match(&I, m_Add(m_Value(X), m_Zero()))", "return replaceInstUsesWith(I, X) trailing;"),
    ]
    for pred, rw in misparse_cases:
        assert pg.recover_pair(pred, rw) is None, ("unmodeled operator must decline, not misparse", rw)
    # a well-formed negative literal argument still parses (the rejection is operators, not `-` literals).
    assert prove("match(&I, m_Add(m_Value(X), m_SpecificInt(-1)))",
                 "return replaceInstUsesWith(I, Builder.CreateSub(X, 1));")[1][0] == "proved", "neg literal arg"

    print("pass_graph_fixture OK: compositional recovery proves a NESTED (X+0)*1->X and a "
          "registry-less or-self (X|X->X); a wrong fold is refuted with a witness; unmodeled "
          "matchers decline; and RECOVERED PRECONDITIONS are load-bearing -- sdiv->udiv refutes "
          "unguarded, proves under both-operands-nonneg, refutes on an insufficient guard, and is "
          "caught vacuous on a contradictory one -- structural DFG/CFG recovery gated by the prover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
