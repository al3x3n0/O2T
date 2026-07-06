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

    print("pass_graph_fixture OK: compositional recovery proves a NESTED (X+0)*1->X and a "
          "registry-less or-self (X|X->X); a wrong fold is refuted with a witness; unmodeled "
          "matchers decline; and RECOVERED PRECONDITIONS are load-bearing -- sdiv->udiv refutes "
          "unguarded, proves under both-operands-nonneg, refutes on an insufficient guard, and is "
          "caught vacuous on a contradictory one -- structural DFG/CFG recovery gated by the prover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
