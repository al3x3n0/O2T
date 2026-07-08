#!/usr/bin/env python3
"""Structured-tree recovery: consume a Clang-AST miner's parsed matcher/rewrite, bypassing the parser.

The recovery's trusted core has two hand-rolled stages: a tokenizer/recursive-descent parser (misparse
risk, hardened but unverified) and the semantic lowering. A Clang-AST miner already has the real C++
AST, so it can emit the matcher and rewrite as STRUCTURED trees ({kind,name,args,template}) directly --
no re-parsing of source substrings. recover_pair now accepts `matcher_tree`/`rewrite_tree`, which
bypasses the tokenizer/parser entirely: on a tree, no misparse is possible.

Pins: (1) the structured path yields the IDENTICAL obligation to the string path; (2) hand-authored
trees (never derived from a string) recover -- including a cast round-trip and a templated intrinsic --
so a miner emitting structure needs no parser; and (3) the structured path proves the same folds.

Needs z3.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t.intent import pass_graph as pg
from o2t import mini_alive as ma


def call(name, *args, template=None):
    return {"kind": "call", "name": name, "template": template, "args": list(args)}


def nm(n):
    return {"kind": "name", "name": n}


def main() -> int:
    z3 = shutil.which("z3") or ("/opt/homebrew/bin/z3" if Path("/opt/homebrew/bin/z3").exists() else None)
    if z3 is None:
        print("pass_graph_structured_fixture: z3 not found, skipped")
        return 0

    # 1. EQUIVALENCE: the string path (which parses C++ substrings) and the structured path (matcher and
    #    rewrite supplied as pre-parsed trees, guards still in the predicate) recover the IDENTICAL
    #    obligation. So moving the parse from O2T into the miner changes nothing downstream.
    string_pair = pg.recover_pair("match(&I, m_SDiv(m_Value(X), m_Value(Y))) && isKnownNonNegative(X) "
                                  "&& isKnownNonNegative(Y)", "return replaceInstUsesWith(I, Builder.CreateUDiv(X, Y));")
    struct_pair = pg.recover_pair(
        "isKnownNonNegative(X) && isKnownNonNegative(Y)", "",
        matcher_tree=pg.parse_source_tree("m_SDiv(m_Value(X), m_Value(Y))"),
        rewrite_tree=pg.parse_source_tree("Builder.CreateUDiv(X, Y)"))
    assert string_pair == struct_pair, "structured recovery must match the string recovery exactly"
    assert ma.prove(struct_pair, z3)[0] == "proved", "structured sdiv->udiv (guarded) proves"

    # 2. PARSER-FREE: hand-authored trees never derived from any source string still recover -- this is
    #    exactly what a Clang miner would hand O2T (a CallExpr -> call node, DeclRefExpr -> name).
    #    (a) a nested cast round-trip trunc(zext(X)) -> X, licensed by the type-equality guard.
    cast = pg.recover_pair(
        "X->getType() == I.getType()", "",
        matcher_tree=call("m_Trunc", call("m_ZExt", call("m_Value", nm("X")))),
        rewrite_tree=nm("X"))
    assert cast is not None and ma.prove(cast, z3)[0] == "proved" and cast["variable_bits"] == {"x": 8}, cast

    #    (b) a templated intrinsic m_Intrinsic<Intrinsic::smin>(X, X) -> X, carried as the `template`
    #        field -- no `<...>` string re-parsing needed.
    smin = pg.recover_pair(
        "", "",
        matcher_tree=call("m_Intrinsic", call("m_Value", nm("X")), call("m_Deferred", nm("X")), template="smin"),
        rewrite_tree=nm("X"))
    assert smin is not None and ma.prove(smin, z3)[0] == "proved", ("structured smin(X,X)->X", smin)

    #    (c) a Builder rewrite as a tree: add(X,Y) -> or(X,Y) needs the disjointness guard; the rewrite
    #        DFG is structured, so the recovered `after` is exactly the or-node.
    disj = pg.recover_pair(
        "haveNoCommonBitsSet(X, Y)", "",
        matcher_tree=call("m_Add", call("m_Value", nm("X")), call("m_Value", nm("Y"))),
        rewrite_tree=call("CreateOr", nm("X"), nm("Y")))
    assert disj is not None and disj["after"]["op"] == "bvor" and ma.prove(disj, z3)[0] == "proved", disj

    # 3. The structured path still soundly DECLINES an unmodeled matcher tree (e.g. m_ICmp condition in a
    #    select) -- structure removes MISparsing, not the modeled-fragment boundary.
    declined = pg.recover_pair(
        "", "", matcher_tree=call("m_Trunc", call("m_Value", nm("X"))), rewrite_tree=nm("X"))
    assert declined is None, "a bare (unguarded) cast still declines without the type-equality guard"

    print("pass_graph_structured_fixture OK: matcher/rewrite supplied as pre-parsed trees recover the "
          "IDENTICAL obligation as the string path and prove the same folds; hand-authored trees (a cast "
          "round-trip, a templated intrinsic, a Builder DFG) recover with no parsing at all -- so a "
          "Clang-AST miner emitting structure removes the tokenizer/parser from the trusted core")
    return 0


if __name__ == "__main__":
    sys.exit(main())
