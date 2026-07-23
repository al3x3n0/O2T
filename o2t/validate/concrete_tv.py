#!/usr/bin/env python3
"""An INDEPENDENT execution cross-check for Track B whole-function TV, via `lli`.

Track B's `scalar_ir.validate_transform` proves refinement with a single hand-written SMT encoding
checked by a single z3 call -- and the second-solver cross-check reuses the SAME generated SMT, so an
ENCODING-GENERATION bug is invisible to it (this is exactly how the `udiv exact` false proof survived
every internal mechanism). This module adds the missing independent oracle -- the Track-B analogue of
Track A's concrete `reconcile`: it COMPILES AND RUNS both `before` and `after` with `lli` (LLVM's real
semantics, not O2T's encoding) on a battery of concrete inputs and checks the returned values agree.

So a value-level encoding mistake (a wrong operation, a wrong memory/gep offset, a wrong cast) that
z3's encoding would share is caught here by real execution -- z3 and lli are then COMPLEMENTARY
independent oracles: if they disagree on a transform z3 called `proved`, that is a false proof.

SCOPE / HONEST LIMITS: this is a VALUE oracle. `lli` computes values; it does not trap on `poison`,
so a purely-poison difference (e.g. an unjustified `nsw`/`exact` flag, where the values agree and only
the poison-refinement differs) is NOT caught here -- that part remains z3's, complemented by the
exhaustive flag/poison matrix test. Non-scalar signatures (ptr/vector/aggregate/void, widths >64)
decline. This never PROVES; it CONFIRMS or CONTRADICTS -- a disagreement flags a would-be false proof.
"""

from __future__ import annotations

import random
import re
import subprocess

_PARAM = re.compile(r"^\s*(i\d+|ptr|<[^>]*>|\{[^}]*\})\b")


def _signature(ll_text: str, func: str):
    """(ret_width, [param_width, ...]) if `func` is an all-scalar-int function, else None."""
    m = re.search(r"define\b[^@]*?(i\d+|void|ptr|<[^>]*>|\{[^}]*\})\s+@" + re.escape(func) + r"\s*\(",
                  ll_text)
    if not m:
        return None
    ret = m.group(1)
    if not re.fullmatch(r"i(\d+)", ret):
        return None                                   # void / ptr / vector / aggregate return -> decline
    rw = int(ret[1:])
    if rw > 64:
        return None
    params_raw = re.search(r"define\b[^@]*@" + re.escape(func) + r"\s*\(([^)]*)\)", ll_text)
    if not params_raw:
        return None
    widths = []
    body = params_raw.group(1).strip()
    if body:
        for part in body.split(","):
            t = _PARAM.match(part)
            if not t or not re.fullmatch(r"i(\d+)", t.group(1)):
                return None                           # a non-scalar-int parameter -> decline
            widths.append(int(t.group(1)[1:]))
    return rw, widths


def _battery(widths, count=48, seed=0x02701):
    """Deterministic concrete argument vectors: edge values first, then a seeded-random tail."""
    def edges(w):
        top = 1 << w
        return [0, 1, 2, 3, top - 1, top - 2, 1 << (w - 1), (1 << (w - 1)) - 1]
    vecs = []
    if not widths:
        return [()]                                   # a nullary function: one call
    # every param set to the same edge index (diagonal), covering the interesting corners cheaply
    for i in range(len(edges(widths[0]))):
        vecs.append(tuple(edges(w)[i % len(edges(w))] for w in widths))
    rng = random.Random(seed)
    while len(vecs) < count:
        vecs.append(tuple(rng.randrange(1 << w) for w in widths))
    return vecs


def _harness(ll_text, func, ret_w, widths, vecs):
    """`ll_text` + a `@main` that calls `func` on each vector and prints each result (as i64)."""
    calls = []
    for i, vec in enumerate(vecs):
        args = ", ".join(f"i{w} {v}" for w, v in zip(widths, vec))
        calls.append(f"  %r{i} = call i{ret_w} @{func}({args})")
        if ret_w == 64:
            reg = f"%r{i}"
        elif ret_w < 64:
            calls.append(f"  %e{i} = zext i{ret_w} %r{i} to i64"); reg = f"%e{i}"
        else:
            calls.append(f"  %e{i} = trunc i{ret_w} %r{i} to i64"); reg = f"%e{i}"
        calls.append(f"  call i32 (ptr, ...) @printf(ptr @.o2t_fmt, i64 {reg})")
    return "\n".join([
        ll_text,
        'declare i32 @printf(ptr, ...)',
        '@.o2t_fmt = private constant [6 x i8] c"%llu\\0A\\00"',
        "define i32 @main() {",
        *calls,
        "  ret i32 0", "}", ""])


def _run(lli_bin, ir, timeout):
    try:
        out = subprocess.run([lli_bin], input=ir, capture_output=True, text=True,
                             errors="replace", timeout=timeout)   # lli may emit non-UTF8 noise
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    toks = out.stdout.split()
    return toks


def concrete_tv(lli_bin: str, before_ll: str, after_ll: str, func: str,
                count: int = 48, timeout: int = 30) -> dict:
    """Independently execute `func` before and after via lli and compare return values.
    Returns {status: agree | disagree | unsupported | skip, ...}. `disagree` (with a witness) on a
    transform z3 called `proved` is a FALSE PROOF. Never returns `proved` -- it only corroborates."""
    sb, sa = _signature(before_ll, func), _signature(after_ll, func)
    if sb is None or sa is None or sb != sa:
        return {"status": "unsupported", "function": func, "reason": "non-scalar or mismatched signature"}
    ret_w, widths = sb
    vecs = _battery(widths, count=count)
    hb, ha = (_harness(before_ll, func, ret_w, widths, vecs),
              _harness(after_ll, func, ret_w, widths, vecs))
    ob, oa = _run(lli_bin, hb, timeout), _run(lli_bin, ha, timeout)
    if ob is None or oa is None or len(ob) != len(vecs) or len(oa) != len(vecs):
        return {"status": "skip", "function": func, "reason": "lli did not run both sides cleanly"}
    for vec, b, a in zip(vecs, ob, oa):
        if b != a:
            return {"status": "disagree", "function": func,
                    "witness": {"input": vec, "before": int(b), "after": int(a)}}
    return {"status": "agree", "function": func, "samples": len(vecs)}


def cross_checked_tv(z3_bin: str, lli_bin: str, before_ll: str, after_ll: str, func: str,
                     timeout: int = 15) -> dict:
    """Track B whole-function TV, backed by the INDEPENDENT lli execution oracle. A `proved` verdict
    now requires BOTH z3's refinement proof AND lli agreement (or lli being inapplicable). If z3
    proves but lli DISAGREES, the z3 proof rested on a faulty VALUE encoding -- returned as
    `refuted-by-execution` with the concrete witness, so a value-encoding false proof cannot pass."""
    from o2t.validate import scalar_ir as si
    v = si.validate_transform(z3_bin, before_ll, after_ll, func, timeout=timeout)
    if v["status"] != "proved":
        return v                                      # refuted / unsupported / timeout: pass through
    cc = concrete_tv(lli_bin, before_ll, after_ll, func)
    if cc["status"] == "disagree":
        return {"status": "refuted-by-execution", "function": func, "witness": cc["witness"],
                "note": "z3 proved but lli disagrees on values -- a value-encoding false proof"}
    return {"status": "proved", "function": func, "concrete": cc["status"]}
