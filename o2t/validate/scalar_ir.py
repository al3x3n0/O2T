#!/usr/bin/env python3
"""Closed-loop translation validation for InstCombine: prove the REAL `opt -passes=instcombine` output.

Extends the real-opt closed loop (indvars / simplifycfg / dse) to scalar peephole combining. It is
a small Alive2-style translation validator: a single-basic-block integer function is translated to
an SMT term for its returned value as a function of the parameters, the actual `opt -passes=
instcombine` is run, the optimized function is translated the same way, and the two return terms
are proved EQUAL for all inputs (QF_BV). So the proof is about the instructions InstCombine really
emitted -- a corrupted fold (e.g. `add`->`sub`) is refuted with a concrete input witness.

Supported (else the function is soundly declined as `unsupported`, never falsely proved): integer
add/sub/mul/and/or/xor, shl/lshr/ashr, udiv/sdiv/urem/srem, icmp (-> i1), select, zext/sext/trunc,
`freeze` (target-side only -- see the `freeze` case in `_instruction`), constants, and a single `ret`.
Every value is modeled as a bitvector of its own width.

The obligation is Alive2-style REFINEMENT, not raw value-equality: alongside each value we
carry a `poison` term (true when the value is poison) and a `ub` term (true when computing it is
undefined behaviour), and prove the optimized function refines the source --
``src_ub  OR  (NOT src_poison) ==> (NOT tgt_ub AND NOT tgt_poison AND src == tgt)``. This makes
the validator catch the poison/UB-introducing miscompiles raw equality misses: a fold that ADDS an
unjustified `nsw`/`nuw`/`exact`/`disjoint` flag, an oversize plain shift, or a freshly introduced
div/rem-by-zero (or `INT_MIN/-1`) is refuted with a witness, while a fold that only DROPS a flag or
removes UB still proves (it is a sound refinement).
"""

from __future__ import annotations

import re
import subprocess

from o2t.formal_ir import VALID_FLAGS, flag_poison_smt, smt_and, smt_or
from o2t.validate import ir_model as ir
from o2t.validate import semantics as sem

# The instruction tables and the poison/UB rules live in the shared semantics layer. These names are
# ALIASES, not copies: `slp_ir`, `mem2reg_ir` and `loop_induction` import them from here, so aliasing
# puts the loop track on the same reading of LLVM as the peephole track without touching their code.
# Duplicate models are what round 6 of the 2026-07 review found a false proof inside; there is now one
# definition, and `semantics_fixture` asserts these were byte-identical before the collapse.
_BIN = sem.BIN
_ICMP = sem.ICMP
_const = sem.const
_own_poison = sem.own_poison
_own_ub = sem.own_ub
_INTRINSICS = sem.INTRINSICS

# Flags whose presence means an operation can produce poison (see `poison_risk`).
_POISON_FLAGS = {"nsw", "nuw", "exact", "disjoint", "nneg"}

# Interprocedural inlining depth: a deeper (or recursive) call chain declines.
_MAX_CALL_DEPTH = 6


# ONE decline type across the stack: the semantics layer raises it, and every caller that catches
# `scalar_ir.Unsupported` keeps working unchanged.
Unsupported = sem.Unsupported


def _const(value, width):
    return f"(_ bv{value % (1 << width)} {width})"


def _params(ll_text, func):
    """Integer parameter name -> width, from the parse. Non-integer parameters are skipped, so a use
    of one declines naturally downstream."""
    fn = ir.parse(ll_text).function(func)
    return fn.int_params if fn else {}


def _noundef_params(ll_text, func):
    """The parameters declared `noundef`. Every other argument may be `undef` at run time, and an
    `undef` value is not one value -- each USE of it may observe a different one. Modeling a parameter
    as a single SMT constant assumes `noundef`, so this set is where that assumption is DECLARED
    rather than assumed (see the undef-risk guard in `validate_transform`)."""
    fn = ir.parse(ll_text).function(func)
    return {p.name for p in fn.params if p.noundef} if fn else set()


def translate(ll_text, func, extra_ops=None, bindings=None, _module=None, _depth=0,
              side="source", fresh=None, param_poison=False):
    """Translate a function to (params, ret_term, ret_width, ret_poison, ret_ub) over LLVM's OWN
    parse. Validated function-by-function against the text reader it replaces over LLVM 18's
    InstCombine tests: 500 identical SMT, 465 identical declines, 0 differences, 0 regressions, and 58
    functions the text reader declined only because of a trailing `; comment`, an `immarg` attribute
    or a `zeroinitializer` -- valid IR its regexes could not match."""
    module = ir.parse(_module if _module is not None else ll_text)
    return _translate_parsed(module, func, extra_ops, bindings, _depth, side, fresh,
                             param_poison)


def _bool_of(term, width):
    """An iW value -> an SMT boolean (true iff nonzero) -- the sense of a branch/select condition."""
    return f"(not (= {term} {_const(0, width)}))"


def target_may_poison(after_ll, func):
    """May the TARGET produce poison? A value-equality validator may only PROVE when it cannot.

    Those validators compare values and have no poison term at all, and `poison_risk` only stops them
    REFUTING (a value mismatch may be a sound poison exploitation). Nothing stopped them PROVING, and
    "value-equal everywhere implies refinement" is FALSE when the target introduces poison -- poison
    is not a value. Two live false proofs found by the synthesized-target fuzzer:

      * a lane-model target adding `exact` to an `lshr` feeding the result: values identical, target
        poison where the source is not;
      * a memory target storing `shl %x, (ashr 1, -1)`, which LLVM makes poison (shift >= width) but
        SMT gives a defined 0, so the stored values looked equal.

    If the target has no poison source and its values agree everywhere, it is defined wherever the
    source is, so refinement genuinely holds -- which is why gating on the TARGET is enough and the
    source may still carry poison."""
    return poison_risk(after_ll, func)


def poison_risk(ll_text, func):
    """Does `func` contain a poison-generating operation that a VALUE-equality validator does not
    refine? A flagged operation, or a shift whose amount is not a scalar in-range constant (a
    variable, an oversize constant, or any vector shift).

    Such a validator may PROVE soundly -- value-equal everywhere implies refinement -- but must NOT
    REFUTE on a value mismatch here, because the mismatch may be a sound poison exploitation (`opt`
    folding a poison `ashr x,x` to 0). Callers decline instead. So the danger is UNDER-approximating:
    missing a poison source turns a sound fold into a false refutation.

    Read from the parse rather than from the body text. The regex this replaces searched the whole
    body for the words `nsw|nuw|exact|disjoint`, which matched them inside COMMENTS -- and LLVM's own
    test files are full of `; CHECK-NEXT: ... add nsw ...` lines, so 63 of 1,023 corpus functions were
    flagged as poison-risky on the strength of a comment. That erred toward declining, so it was safe,
    but it silently suppressed refutations. `nneg` is included here and was in neither the old word
    list nor its intent: it is a genuine LLVM 18 poison flag, so omitting it was the dangerous
    direction."""
    fn = ir.parse(ll_text).function(func)
    if fn is None:
        return False
    for inst in fn.instructions():
        if _POISON_FLAGS & set(inst.flags):
            return True
        if inst.op in ("shl", "lshr", "ashr"):
            if inst.type.kind == "vector":
                # A vector shift is risky unless EVERY lane's amount is a visible in-range constant.
                # Treating them all as risky was safe but, now that this also gates PROVING, it
                # declined every vector function containing a shift at all.
                amount, width = inst.operands[1], inst.type.elem.bits if inst.type.elem else None
                if width is None:
                    return True
                if amount.kind == "splat":
                    e = amount.splat_elem
                    if e is None or e.kind != "int" or not (0 <= e.int_value < width):
                        return True
                elif amount.kind == "vector":
                    for e in amount.elements:
                        if e.kind != "int" or not (0 <= e.int_value < width):
                            return True
                else:
                    return True                        # a variable or opaque lane-wise amount
                continue
            amount = inst.operands[1]
            if amount.kind != "int" or not (0 <= amount.int_value < inst.type.bits):
                return True                            # variable or out-of-range scalar shift
    return False


def run_passes(src_text, passes, opt_bin="opt"):
    """Run any `opt -passes=<passes>` pipeline and return the textual IR (or None on failure)."""
    proc = subprocess.run([opt_bin, f"-passes={passes}", "-S", "-o", "-"],
                          input=src_text, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def run_instcombine(src_text, opt_bin="opt"):
    return run_passes(src_text, "instcombine", opt_bin)


def _query(z3_bin, smt, timeout):
    """Run one SMT-LIB2 query through z3; return (first-result-line, full stdout). A timeout raises
    subprocess.TimeoutExpired so the caller can decline rather than guess."""
    out = subprocess.run([z3_bin, "-in"], input=smt, capture_output=True, text=True,
                         timeout=timeout).stdout
    return (out.strip().splitlines()[0].strip() if out.strip() else "error"), out


def _mentions(name, *terms):
    """Does this parameter's SMT constant appear in any of these terms? Word-boundary matched so `%x`
    does not match `%x1`."""
    pat = re.compile(re.escape(name) + r"(?![\w.])")
    return any(pat.search(t) for t in terms if t)


def _smt(decls, goal, get_model=False, forall=()):
    """One SMT-LIB2 query. `forall` binds SOURCE-side nondeterministic choices.

    The refutation asked here is `exists input, exists target-choice, forall source-choice. ...`, and
    the polarity is not cosmetic: a TARGET choice is existential (a free constant the solver picks to
    expose the miscompile) while a SOURCE choice is universal (the target must differ from EVERY
    value the source could have produced). Binding a source choice as a free constant instead would
    let the solver CHOOSE the source's value to make the two differ, which manufactures false
    REFUTATIONS. Quantifying costs the quantifier-free logic -- `BV` is still decidable, and an
    `unknown` from the solver is reported as such rather than guessed at."""
    logic = "BV" if forall else "QF_BV"
    if forall:
        binders = " ".join(f"({n} (_ BitVec {w}))" for n, w in forall)
        goal = f"(forall ({binders}) {goal})"
    lines = [f"(set-logic {logic})", *decls, f"(assert {goal})", "(check-sat)"]
    if get_model:
        lines.append("(get-model)")
    return "\n".join(lines) + "\n"


def cross_check_smt(smt, expect, z3_bin=None, extra_solvers=()):
    """Replay one query through every OTHER SMT-LIB2 solver on PATH (bitwuzla/cvc5/cvc4) and report
    whether they all reproduce `expect` (sat|unsat). Track B's verdict is a single z3 call over a
    hand-built encoding: the encoding is cross-checked by lli/Alive2, but the SOLVER is not. Replaying
    the IDENTICAL script through an independently implemented solver closes that hole -- a
    disagreement is a solver (or SMT-LIB) bug, not an encoding bug, and no other oracle can see it.
    Reported `skipped` (honest) when no second solver is installed, never silently passed."""
    from o2t.meta.cross_check import detect_solvers, run_solver     # lazy: avoids an import cycle
    solvers = [(n, b) for n, b in detect_solvers(z3_bin or "z3", extra_solvers) if n != "z3"]
    if not solvers:
        return {"status": "skipped", "reason": "no second solver on PATH", "solvers": {}}
    results = {name: run_solver(name, binary, smt) for name, binary in solvers}
    agree = all(r == expect for r in results.values())
    return {"status": "agree" if agree else "disagree", "expect": expect, "solvers": results}


def validate_transform(z3_bin, src_text, opt_text, func, timeout=None, extra_ops=None,
                       check_vacuity=True, cross_check=False, extra_solvers=()):
    """Translate before/after and prove the returned value equal for all inputs -- a closed-loop
    translation validation for ANY value-preserving scalar pass (instcombine, reassociate,
    early-cse, gvn, ...). Returns a verdict dict (status proved|refuted|unsupported|error|timeout).
    `timeout` (seconds) bounds the z3 call so one pathological function cannot hang a corpus sweep --
    a timeout is a sound DECLINE (no verdict), never a proof. `extra_ops` are validated enrichment
    handlers (o2t/validate/enrich.py) that widen the modeled instruction set.

    `check_vacuity` (default on) probes whether the SOURCE is defined anywhere. Refinement is
    vacuously true when the source is UB or poison on EVERY input -- `udiv %x, 0` legitimately
    refines to `ret 12345` -- so such a `proved` is valid but carries no information about the
    transform. It is also the exact signature of an OVER-APPROXIMATED UB/poison model: claiming UB
    where LLVM has none turns a would-be refutation into a proof -- the same failure SHAPE as the two
    false proofs the 2026-07 review found by hand (a model corner that silently converts a refutation
    into a proof), and the one shape the encoding oracles cannot see, since lli and Alive2 are
    consulted only on the proved set and agree that a UB source refines to anything. The verdict
    carries `vacuous: True|False|None` (None = the probe was inconclusive); Track A has had this guard
    since mini_alive's premise-satisfiability check, Track B had none.

    `cross_check` replays the decided query through a second, independently implemented solver."""
    fresh: list = []                                   # nondeterministic choices (freeze), declared below
    try:
        p0, r0, w0, sp, su = translate(src_text, func, extra_ops, side="source", fresh=fresh,
                                       param_poison=True)
        p1, r1, w1, tp, tu = translate(opt_text, func, extra_ops, side="target", fresh=fresh,
                                       param_poison=True)
    except Unsupported as exc:
        return {"status": "unsupported", "function": func, "reason": str(exc)}
    except ir.IrParseError as exc:
        return {"status": "error", "function": func,
                "reason": f"module is not valid LLVM IR: {str(exc).splitlines()[0][:120]}"}
    if p0 != p1 or w0 != w1:
        return {"status": "error", "function": func, "reason": "signature changed"}

    # UNDEF-RISK GUARD. Every parameter is modeled as ONE definite SMT constant, which silently
    # assumes `noundef` on every argument. LLVM does not: an argument may be `undef`, and an `undef`
    # value is not one value -- each USE of it may observe a different one. The assumption becomes
    # LOAD-BEARING exactly when the TARGET's result depends on such a parameter and the SOURCE's does
    # not: the source is then determined where the target is not, so the target has behaviours the
    # source lacks. `ret i32 0 -> xor %x, %x` is the canonical case -- it PROVED here (both sides are
    # 0 under one constant) while reference Alive2 refutes it, and adding `noundef %x` makes Alive2
    # prove it, which pins the mechanism. Neither the lli nor the Alive2 oracle catches this in the
    # corpus sweeps, because real InstCombine never introduces a duplicated argument use; it is
    # reachable through this API, which compose_tv/module_tv/argprom_tv and user passes all go through.
    # Measured cost on LLVM 18 and/or/xor/add/select/freeze.ll: 0 of 447 proofs (the 10 functions
    # where `opt` legitimately multiplies a parameter use all have a source that already depends on
    # it, and Alive2 confirms all 10 sound).
    # The test is on the returned VALUE and its poison, not on UB: UB is checked existentially over
    # the parameter's whole range either way (`udiv %a, %b` is UB for some `%b` whether that `%b` is
    # one constant or undef), so including it only over-declines -- it wrongly declined the
    # introduce-a-dead-div-by-zero teeth, which must still refute.
    risky = [n for n in sorted(p0) if n not in _noundef_params(src_text, func)
             and _mentions(n, r1, tp) and not _mentions(n, r0, sp)]
    if risky:
        # Tagged so the Track B DISPATCHER can tell this decline apart from "this validator does not
        # model that shape". It is a statement about the TRANSFORM, not about this validator, so
        # handing the same pair to another one must not be allowed to overturn it.
        return {"status": "unsupported", "function": func, "guard": "undef-risk",
                "reason": f"target result depends on possibly-undef parameter(s) "
                          f"{', '.join(risky)} the source result does not (add `noundef` to declare "
                          f"them defined; an undef argument may read differently at each use)"}

    # A nondeterministic choice is declared FREE (existential) when the target makes it, and BOUND by
    # a universal quantifier when the source does -- see `_smt`. The side is recorded in the name at
    # the point the choice is created.
    src_fresh = [(n, w) for n, w in fresh if n.endswith("_source")]
    tgt_fresh = [(n, w) for n, w in fresh if not n.endswith("_source")]
    decls = [f"(declare-const {name} (_ BitVec {w}))" for name, w in sorted(p0.items())]
    decls += [f"(declare-const {name} (_ BitVec {w}))" for name, w in tgt_fresh]
    # ...and one boolean per parameter that may arrive POISON. Shared by both sides, because it
    # describes the INPUT rather than a choice either side makes.
    decls += [f"(declare-const {param_poison_flag(n)} Bool)"
              for n in sorted(set(p0) - _noundef_params(src_text, func))]
    # Alive2 refinement refutation: an input where the source is defined (no UB, value not poison)
    # but the target misbehaves -- it is UB, becomes poison, or returns a different value. (A pass
    # that only DROPS a flag / removes UB cannot satisfy this, so it still proves.)
    refute = smt_and([f"(not {su})",
                      smt_or([tu, smt_and([f"(not {sp})",
                                           smt_or([tp, f"(not (= {r0} {r1}))"])])])])
    smt = _smt(decls, refute, get_model=True, forall=src_fresh)
    try:
        head, out = _query(z3_bin, smt, timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "function": func}
    if head == "unsat":
        verdict = {"status": "proved", "function": func}
    elif head == "sat":
        verdict = {"status": "refuted", "function": func, "witness": out}
    else:
        return {"status": "error", "function": func, "reason": head}

    if check_vacuity and head == "unsat":
        # Is the source defined on ANY input? sat => the proof is about real behaviour.
        # existential in this probe: "is the source defined for SOME input and SOME choice"
        defined = smt_and([f"(not {su})", f"(not {sp})"])
        vdecls = decls + [f"(declare-const {n} (_ BitVec {w}))" for n, w in src_fresh]
        try:
            dhead, _ = _query(z3_bin, _smt(vdecls, defined), timeout)
        except subprocess.TimeoutExpired:
            dhead = "timeout"
        verdict["vacuous"] = {"sat": False, "unsat": True}.get(dhead)   # None: inconclusive probe
    if cross_check:
        verdict["cross_check"] = cross_check_smt(smt, head, z3_bin, extra_solvers)
    return verdict


def validate_instcombine(z3_bin, src_text, opt_text, func):
    """Backward-compatible alias: InstCombine is one value-preserving scalar pass."""
    return validate_transform(z3_bin, src_text, opt_text, func)


def function_names(ll_text):
    return ir.parse(ll_text).defined_names


# =================================================================================================
# The PARSED translator: LLVM's own parse (ir_model) + the shared semantics layer.
#
# This replaces the text reader above. It is a straight port -- same 5-tuple, same SMT strings, same
# declines -- validated against the text path function-by-function over LLVM's own InstCombine tests
# (see `translate_ab` and parsed_translate_fixture). What changes is not what is modeled but how the
# module is READ: by LLVM, so a call site above a definition cannot be mistaken for a signature, an
# attribute containing a comma cannot truncate a parameter list, and an unmodeled opcode DECLINES on
# its opcode instead of a regex quietly failing to match.
# =================================================================================================

def _p_value(v, env, width=None):
    return sem.value(v, env, width)


def _p_local_memory(inst, env, ctx):
    """The local-alloca model: a non-escaping `alloca` is a cell, a `store` updates it, a `load` reads
    the last store. An escaped pointer is never a value in `env`, so its use declines naturally and no
    aliasing is ever assumed. Returns True when the instruction was consumed."""
    mem = ctx.get("mem")
    if mem is None:
        return False
    if inst.op == "alloca":
        if not (inst.alloc_type and inst.alloc_type.is_int()):
            raise sem.Unsupported("alloca of a non-integer type")
        mem["cell"][inst.result] = len(mem["cell"])
        return True
    if inst.op == "store":
        val, ptr = inst.operands[0], inst.operands[1]
        cell = mem["cell"].get(ptr.name) if ptr.is_reg else None
        if cell is None:
            raise sem.Unsupported("store to a non-local/escaped pointer")
        w = val.type.bits if val.type.is_int() else None
        if w is None:
            raise sem.Unsupported("store of a non-integer value")
        vt, _, vp, _ = _p_value(val, env, w)
        mem["val"][cell] = (vt, vp)
        return True
    if inst.op == "load":
        ptr = inst.operands[0]
        cell = mem["cell"].get(ptr.name) if ptr.is_reg else None
        if cell is None or cell not in mem["val"]:
            raise sem.Unsupported("load from an escaped/uninitialized pointer")
        vt, vp = mem["val"][cell]
        env[inst.result] = (vt, sem.int_width(inst.type), vp, "false")
        return True
    return False


def _p_call_defined(inst, env, ctx):
    """A direct call to a module-DEFINED function is inlined by translating the callee with its
    parameters bound to the argument terms. Recursion and over-deep chains decline."""
    if inst.op != "call" or inst.indirect or sem.intrinsic_name(inst.callee) is not None:
        return False
    module = ctx["module"]
    callee = module.function(inst.callee) if inst.callee else None
    if callee is None or callee.is_declaration:
        return False                                   # declared/external -> extra_ops or decline
    if ctx["depth"] >= _MAX_CALL_DEPTH:
        raise sem.Unsupported("call too deep / recursion")
    cparams = callee.int_params
    if len(inst.args) != len(cparams):
        raise sem.Unsupported("call arity mismatch")
    bindings = {}
    for (pname, pw), arg in zip(cparams.items(), inst.args):
        bindings[pname] = _p_value(arg, env, pw)
    _, cret, cw, cp, cu = _translate_parsed(module, callee.name, ctx["extra_ops"], bindings,
                                            ctx["depth"] + 1, ctx.get("side", "source"),
                                            ctx.get("fresh"))
    env[inst.result] = (cret, cw, cp, cu)
    return True


def _p_instruction(inst, env, ctx):
    """One instruction: local memory, then an inlined call, then the shared semantics, then the
    lli-validated enrichment handlers. Anything left over declines on its OPCODE."""
    if _p_local_memory(inst, env, ctx):
        return
    if _p_call_defined(inst, env, ctx):
        return
    try:
        sem.evaluate(inst, env, ctx)
        return
    except sem.Unsupported:
        for handler in (ctx.get("extra_ops") or ()):   # validated enrichments (enrich.py)
            result = handler(inst, env)
            if result is not None:
                env[inst.result] = result
                return
        raise


def _p_multiblock(fn, params, env, ctx):
    """Symbolically execute an ACYCLIC CFG. Each block carries a path condition, a `phi` lowers to an
    `ite` over its predecessors' reached-from conditions, and returns are combined by path condition.
    Sound by scope: div/rem decline (so whole-function UB stays `false` and needs no path
    conditioning), and a back-edge declines. A conditional branch on a POISON condition is undefined
    behaviour, so its poison is accumulated into the result -- discarding it caused a false
    REFUTATION the CFG fuzzer found."""
    order = [b.name for b in fn.blocks]
    binfo = {b.name: b for b in fn.blocks}
    succ = {}
    for b in fn.blocks:
        body = b.instructions
        if any(i.op in ("udiv", "sdiv", "urem", "srem") for i in body[:-1]):
            raise sem.Unsupported("div/rem in multi-block (UB path-conditioning not modeled)")
        term = body[-1] if body else None
        if term is None:
            raise sem.Unsupported("empty block")
        if term.op == "ret":
            succ[b.name] = []
        elif term.op == "br":
            succ[b.name] = list(term.successors)
        else:
            raise sem.Unsupported(f"terminator {term.op!r}")
    preds = {lab: [] for lab in order}
    for lab in order:
        for s in succ[lab]:
            if s not in preds:
                raise sem.Unsupported(f"branch to unknown block %{s}")
            preds[s].append(lab)

    path, edge, rets, branch_poison = {order[0]: "true"}, {}, [], []
    done, todo, progress = set(), list(order), True
    while todo and progress:
        progress = False
        for lab in list(todo):
            if lab != order[0] and any(p not in done for p in preds[lab]):
                continue
            if lab != order[0]:
                parts = [f"(and {path[p]} {edge[(p, lab)]})" for p in preds[lab]]
                path[lab] = parts[0] if len(parts) == 1 else "(or " + " ".join(parts) + ")"
            body = binfo[lab].instructions
            for inst in body[:-1]:
                if inst.op == "phi":
                    w = sem.int_width(inst.type)
                    val = poi = None
                    for value, plab in inst.incoming:
                        vt, _, vp, _ = _p_value(value, env, w)
                        rf = f"(and {path.get(plab, 'false')} {edge.get((plab, lab), 'false')})"
                        val = vt if val is None else f"(ite {rf} {vt} {val})"
                        poi = vp if poi is None else f"(ite {rf} {vp} {poi})"
                    env[inst.result] = (val, w, poi, "false")
                    continue
                _p_instruction(inst, env, ctx)
            term = body[-1]
            if term.op == "ret":
                if not term.operands:
                    raise sem.Unsupported("void return")
                w = sem.int_width(term.operands[0].type)
                rt, _, rp, _ = _p_value(term.operands[0], env, w)
                rets.append((rt, rp, w, path[lab]))
            elif term.conditional:
                cv, _, cvp, _ = _p_value(term.operands[0], env, 1)
                if cvp != "false":                     # branching on POISON poisons the whole result
                    branch_poison.append(f"(and {path[lab]} {cvp})")
                cb = _bool_of(cv, 1)
                edge[(lab, term.successors[0])] = cb
                edge[(lab, term.successors[1])] = f"(not {cb})"
            else:
                edge[(lab, term.successors[0])] = "true"
            done.add(lab); todo.remove(lab); progress = True
    if todo:
        raise sem.Unsupported("cyclic CFG (loop) -- not modeled")
    if not rets:
        raise sem.Unsupported("no scalar ret")
    w = rets[0][2]
    term, poison = rets[-1][0], rets[-1][1]
    for rt, rp, _, pc in reversed(rets[:-1]):
        term, poison = f"(ite {pc} {rt} {term})", f"(ite {pc} {rp} {poison})"
    if branch_poison:
        poison = smt_or([poison, *branch_poison])
    return params, term, w, poison, "false"


def _undef_free(fn):
    """The registers whose value provably cannot be `undef` -- the second lattice level, as far as it
    is needed.

    `undef` is not one value: each USE of it may observe a different one, which is why it cannot be a
    single SMT term and why this model declines it. But a great many values are provably free of that
    freedom, and knowing WHICH is enough to decide the one question that kept declining: whether a
    source-side `freeze` has anything to collapse.

    Computed syntactically, and deliberately UNDER-approximated -- a value is admitted only when every
    operand it derives from is admitted. `and %x, 0` is really defined whatever `%x` is, and is not
    admitted here; missing it costs a decline, never a proof. A call result is never admitted (a
    callee may return `undef`), and `freeze` is admitted unconditionally, which is exactly what freeze
    does.
    """
    free = {name for name, p in ((p.name, p) for p in fn.params) if p.noundef}

    def operand_free(v):
        if v.is_reg:
            return v.name in free
        return not v.is_undef                      # a constant (or `poison`) has no undef freedom

    for blk in fn.blocks:
        for inst in blk.instructions:
            dst = getattr(inst, "result", None)
            if not dst:
                continue
            if inst.op == "freeze":
                free.add(dst)                      # the point of freeze: the freedom stops here
            elif inst.op != "call" and inst.operands and all(operand_free(o) for o in inst.operands):
                free.add(dst)
    return free


def param_poison_flag(name):
    """The SMT boolean naming whether the argument bound to `name` was passed POISON.

    Deterministic, because both sides must use the SAME flag: an argument's poison-ness is a property
    of the INPUT, not a nondeterministic choice either side gets to make, so source and target see one
    flag and the solver picks it once per counterexample."""
    return f"pois_{name}"


def _translate_parsed(module, func, extra_ops=None, bindings=None, _depth=0,
                      side="source", fresh=None, param_poison=False):
    """`translate` over a real parse. `module` is an `ir_model.Module`."""
    fn = module.function(func)
    if fn is None or fn.is_declaration:
        raise sem.Unsupported(f"function {func} not found")
    params = fn.int_params
    # A parameter without `noundef` may be passed POISON as well as `undef`, and modelling it as
    # definitely-not-poison flatters the TARGET: it can return that parameter, look defined, and be
    # poison in reality. Reference Alive2 refutes `freeze %x -> %x` for exactly this reason, and its
    # witness is `%x = poison`, not an undef one. Opt-in so that only the validator that also
    # DECLARES the flags emits them; every other caller keeps the previous model verbatim.
    nou = {p.name for p in fn.params if p.noundef} if param_poison else set(params)
    env = dict(bindings) if bindings is not None else \
        {name: (name, w, "false" if name in nou else param_poison_flag(name), "false")
         for name, w in params.items()}
    ctx = {"module": module, "depth": _depth, "extra_ops": extra_ops, "side": side, "fresh": fresh,
           "undef_free": _undef_free(fn)}

    if len(fn.blocks) > 1:
        return _p_multiblock(fn, params, env, ctx)

    ctx["mem"] = {"cell": {}, "val": {}}
    ret_term = ret_width = None
    ret_poison = ret_ub = "false"
    for inst in fn.blocks[0].instructions:
        if inst.op == "ret":
            if not inst.operands:
                raise sem.Unsupported("void return")
            if not inst.operands[0].type.is_int():
                raise sem.Unsupported("no scalar ret")
            ret_width = sem.int_width(inst.operands[0].type)
            ret_term, _, ret_poison, ret_ub = _p_value(inst.operands[0], env, ret_width)
            break
        _p_instruction(inst, env, ctx)
    if ret_term is None:
        raise sem.Unsupported("no scalar ret")
    # UB is a whole-function property: a div-by-zero anywhere is UB even if its result is dead.
    func_ub = smt_or([ret_ub, *(v[3] for v in env.values())])
    return params, ret_term, ret_width, ret_poison, func_ub
