#!/usr/bin/env python3
"""Unit coverage for the loop->closed-form FORMAL validator (closed_form.py), z3-only.

Locks the pieces the end-to-end translation_validation_fixture exercises but without
needing `opt`: the closed-form SCEV parser, the integer lowering of smax (as `ite`), the
canonical counted-loop recognizer, and the two-sided discharge (a correct closed form
PROVES over all n; a corrupted one is REFUTED with a concrete witness)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.validate import closed_form as cf


def main() -> int:
    z3 = shutil.which("z3")
    if z3 is None:
        print("closed_form_fixture: z3 not found, skipped")
        return 0

    # 1) parser: `(-1 + (1 smax %n)) * %c` -> DSL with a smax node.
    expr = cf.parse_closed_form("((-1 + (1 smax %n))<nsw> * %c)")
    assert expr == cf._op("mul",
                          cf._op("add", cf._const(-1), cf._op("smax", cf._const(1), cf._var("n"))),
                          cf._var("c")), expr
    # the indvars half-product widening parses into the trunc/udiv/zext idiom (N-ary matcher).
    wide = cf.parse_closed_form("(trunc i33 (((zext i32 (%a) to i33) * (zext i32 (%b) to i33)) /u 2) to i32)")
    assert cf.match_widening(wide) == {"factors": [cf._var("a"), cf._var("b")], "k": 2, "to": 32}, wide
    # a THREE-factor product widening (the cubic case) also matches -- consecutive factors so the
    # product is divisible by 2 (the lemma is unsound for arbitrary, possibly-odd, products).
    def sm():
        return cf._op("smax", cf._var("n"), cf._const(1))
    def tk(k):
        return cf._op("sub", sm(), cf._const(k))
    def zx(e):
        return {"op": "zext", "from": 32, "to": 33, "args": [e]}
    wide3 = {"op": "trunc", "from": 33, "to": 32, "args": [cf._op("udiv",
             cf._op("mul", cf._op("mul", zx(tk(1)), zx(tk(2))), zx(tk(3))), cf._const(2))]}
    m3 = cf.match_widening(wide3)
    assert m3 is not None and len(m3["factors"]) == 3 and m3["k"] == 2, m3
    # a surviving recurrence is still out of scope.
    try:
        cf.parse_closed_form("{0,+,1}<%loop>")
        assert False, "addrec should decline"
    except cf._Unsupported:
        pass

    # 2) integer lowering: smax -> commutative-canonical ite (division-free).
    assert cf.lower_int(cf._op("smax", cf._var("n"), cf._const(1))) == "(ite (>= 1 n) 1 n)"

    # 3) source recognizer on the canonical counted do-while (matches the real fixture shape).
    src = """define i32 @f(i32 %c, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %acc.next = add i32 %acc, %c
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}"""
    model = cf.recognize_source_loop(src, "f")
    assert model is not None and model["bound"] == "n", model
    # constant delta c -> polynomial {0: c}
    assert model["acc0"] == cf._const(0) and model["delta"] == {0: cf._var("c")}, model
    # exit value = acc0 + c*(smax(n,1) - 1)
    src_exit = cf.source_exit_value(model)

    # 4) discharge: the correct closed form PROVES for all n; corruptions REFUTE with a witness.
    def sm():
        return cf._op("smax", cf._var("n"), cf._const(1))
    correct = cf._op("mul", cf._var("c"), cf._op("sub", sm(), cf._const(1)))
    assert cf.prove_equal(z3, src_exit, correct)[0] == "proved"

    off_by_one = cf.prove_equal(z3, src_exit, cf._op("mul", cf._var("c"), sm()))
    assert off_by_one[0] == "refuted" and off_by_one[1]["counterexample"], off_by_one
    wrong_factor = cf.prove_equal(
        z3, src_exit,
        cf._op("mul", cf._op("mul", cf._const(2), cf._var("c")), cf._op("sub", sm(), cf._const(1))))
    assert wrong_factor[0] == "refuted" and wrong_factor[1]["counterexample"], wrong_factor

    # 5) AFFINE delta `acc += a*i + b` -> (c1, c0) = (a, b); an shl delta is DECLINED (only
    #    mul/add modeled), not mistaken for an i-free parameter (soundness).
    affine = src.replace("  %acc.next = add i32 %acc, %c",
                         "  %ai = mul i32 %a, %i\n  %t = add i32 %ai, %b\n  %acc.next = add i32 %acc, %t")
    am = cf.recognize_source_loop(affine, "f")
    assert am is not None and set(am["delta"]) == {0, 1}, ("affine delta a*i+b not recognized", am)
    # coefficients may be unsimplified (a*1+0); check SEMANTICALLY.
    assert cf.prove_equal(z3, am["delta"][1], cf._var("a"))[0] == "proved", ("c1 != a", am["delta"][1])
    assert cf.prove_equal(z3, am["delta"][0], cf._var("b"))[0] == "proved", ("c0 != b", am["delta"][0])
    # a QUADRATIC delta i*i -> {2: 1} (cubic closed form, within the degree ceiling).
    quad = src.replace("  %acc.next = add i32 %acc, %c",
                       "  %sq = mul i32 %i, %i\n  %acc.next = add i32 %acc, %sq")
    qm = cf.recognize_source_loop(quad, "f")
    assert qm is not None and set(qm["delta"]) == {2}, ("quadratic delta not recognized", qm)
    # a CUBIC delta i*i*i -> {3: 1} (quartic closed form). Degree 4 is beyond the ceiling -> decline.
    cub = src.replace("  %acc.next = add i32 %acc, %c",
                      "  %p2 = mul i32 %i, %i\n  %p3 = mul i32 %p2, %i\n  %acc.next = add i32 %acc, %p3")
    cm = cf.recognize_source_loop(cub, "f")
    assert cm is not None and set(cm["delta"]) == {3}, ("cubic delta not recognized", cm)
    quartic = src.replace("  %acc.next = add i32 %acc, %c",
                          "  %p2 = mul i32 %i, %i\n  %p4 = mul i32 %p2, %p2\n  %acc.next = add i32 %acc, %p4")
    assert cf.recognize_source_loop(quartic, "f") is None, "degree-4 delta must DECLINE (beyond ceiling)"
    shl = src.replace("  %acc.next = add i32 %acc, %c",
                      "  %d = shl i32 %i, 1\n  %acc.next = add i32 %acc, %d")
    assert cf.recognize_source_loop(shl, "f") is None, "shl delta must DECLINE, not be read as i-free"

    # 6) WIDENING lemma: trunc_i32((zext(a2)*zext(a1)) /u 2) == (a1*a2)/2 (mod 2^32), proved
    #    modularly; then the abstracted half makes the surrounding poly identity discharge.
    a1 = cf._op("sub", sm(), cf._const(1))                 # T-1
    a2 = cf._op("sub", sm(), cf._const(2))                 # T-2
    z1 = {"op": "zext", "from": 32, "to": 33, "args": [a1]}
    z2 = {"op": "zext", "from": 32, "to": 33, "args": [a2]}
    widen = {"op": "trunc", "from": 33, "to": 32,
             "args": [cf._op("udiv", cf._op("mul", z2, z1), cf._const(2))]}
    abstract, lemmas = cf.abstract_widenings(z3, widen)
    assert lemmas == [True] and abstract["op"] == "divprod", (lemmas, abstract)
    # sumProduct-shaped closed form: source c*divprod == opt divprod*c, proved via the abstraction.
    src_q = cf._op("mul", cf._var("c"), cf._divprod([a1, a2], 2))
    opt_q = cf._op("mul", abstract, cf._var("c"))
    assert cf.prove_equal(z3, src_q, opt_q)[0] == "proved"
    assert cf.prove_equal(z3, src_q, cf._op("mul", abstract, cf._var("n")))[0] == "refuted"  # teeth

    # 7) N-ary widening: the 3-factor /u 2 lemma (the cubic case) is proved modularly.
    assert cf.prove_widening_lemma(z3, wide3, m3) is True, "3-factor widening lemma must hold"

    # 8) OPTIMIZER-SIDE teeth: a corrupted *optimized* closed form (a simulated indvars bug)
    #    must be REFUTED. Source = Σi² (cubic); opt = the real indvars closed form, then mutated.
    import copy
    cubic_src = src.replace("  %acc.next = add i32 %acc, %c",
                            "  %sq = mul i32 %i, %i\n  %acc.next = add i32 %acc, %sq")
    cubic_model = cf.recognize_source_loop(cubic_src, "f")
    cubic_source = cf.source_exit_value(cubic_model)
    opt_scev = ("((trunc i33 (((zext i32 (-2 + (1 smax %n)) to i33) * "
                "(zext i32 (-1 + (1 smax %n)) to i33)) /u 2) to i32) + (1431655766 * "
                "(trunc i33 (((zext i32 (-3 + (1 smax %n)) to i33) * "
                "(zext i32 (-2 + (1 smax %n)) to i33) * "
                "(zext i32 (-1 + (1 smax %n)) to i33)) /u 2) to i32)))")
    opt_abs, lem = cf.abstract_widenings(z3, cf.parse_closed_form(opt_scev))
    assert all(lem) and cf.prove_equal(z3, cubic_source, opt_abs)[0] == "proved", "baseline cubic must prove"

    def corrupt(mut):
        bad = copy.deepcopy(opt_abs)
        mut(bad)
        return cf.prove_equal(z3, cubic_source, bad)[0]

    def set_magic(o):           # wrong modular-inverse constant
        for a in o["args"]:
            if a["op"] == "mul" and a["args"][0].get("op") == "const":
                a["args"][0]["value"] += 1
    def bump_divisor(o):        # wrong /u K
        def rec(n):
            if isinstance(n, dict):
                if n.get("op") == "divprod":
                    n["k"] *= 2
                    return True
                return any(rec(a) for a in n.get("args", []))
            return False
        rec(o)
    def drop_factor(o):         # wrong product arity
        def rec(n):
            if isinstance(n, dict):
                if n.get("op") == "divprod" and len(n["factors"]) == 3:
                    n["factors"] = n["factors"][:2]
                    return True
                return any(rec(a) for a in n.get("args", []))
            return False
        rec(o)
    for name, mut in (("magic-const", set_magic), ("divisor", bump_divisor), ("arity", drop_factor)):
        assert corrupt(mut) == "refuted", f"corrupted optimized closed form ({name}) NOT refuted"

    # 9) a non-canonical loop (no acc phi returned) declines, not crashes.
    assert cf.recognize_source_loop("define i32 @g() {\nentry:\n  ret i32 0\n}", "g") is None

    print("closed_form_fixture OK: affine..cubic closed forms (N-ary zext/trunc/udiv widening) proved "
          "over all n; widening lemmas hold; source- AND optimizer-side corruptions refuted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
