#!/usr/bin/env python3
"""The reach measurement is reproducible, and it cannot report "nothing to do" for work it can't name.

`tools/cv-symexec-reach-sweep.py` answers the question the symexec track is steered by: of the
fold-shaped functions in a real InstCombine file, which COMPILE against the symbolic shim, and what
blocks the rest. That number has been quoted for several sessions from a measurement run by hand and
never checked in -- so nobody could re-derive it, and every "what to model next" argument rested on
it. The sweep needs real LLVM source, which is not in the tree; what is gated here is everything
about it that does NOT: the extractor, the classifier, and the accounting.

The accounting is the part with teeth. A fold whose clang errors name no identifier is not a fold
with nothing missing -- it is a fold the shim already has the vocabulary for and gets the SHAPE
wrong (a `Value` returned by value where upstream binds `Value *`, an APInt without the operator the
fold uses). Reported as an empty blocker list, those sort to the top as the easiest folds in the
file when they are a different kind of work entirely. That is exactly what the first run of this
tool did, and two real folds hid behind it.

Needs clang++; self-skips without it.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("reach_sweep", ROOT / "tools" / "cv-symexec-reach-sweep.py")
sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw)

# A miniature of the real thing: fold-shaped definitions, a declaration, a non-fold, a method, a
# pass-local helper, and braces inside a string and a comment to defeat naive counting.
SAMPLE = r'''
static Value *helperUsedByAFold(Value *X);      // a declaration -- not a definition

static Value *foldThatCompiles(BinaryOperator &I, IRBuilderBase &Builder) {
  Value *X;
  if (match(&I, m_Xor(m_Value(X), m_AllOnes())))   // "} not a brace {"
    return Builder.CreateNot(X);                   /* nor { this } one */
  return nullptr;
}

static bool notAFold(Value *X) { return X != nullptr; }   // returns bool -- no rewrite to discharge

void alsoNotAFold(Instruction &I) { (void)I; }

static Value *foldNeedingVocabulary(BinaryOperator &I) {
  KnownBits Known = computeKnownBits(&I);
  return totallyUndeclaredThing(Known);
}

static Value *foldNeedingAPassLocalHelper(BinaryOperator &I) {
  return helperUsedByAFold(&I);
}

Value *InstCombinerImpl::foldDefinedAsAMethod(BinaryOperator &I) {
  Value *X;
  if (match(&I, m_Xor(m_Value(X), m_AllOnes())))
    return Builder.CreateNot(X);
  return nullptr;
}

static Value *helperUsedByAFold(Value *X) { return X; }
'''

# Names nothing; the shim HAS a freeze, this simply calls it in a shape the shim does not offer.
SHAPE_ONLY = r'''
static Value *foldWithAShapeMismatch(BinaryOperator &I, IRBuilderBase &Builder) {
  Value V = Builder.CreateFreeze(&I);     // Value* where a Value is required: a shape error, not a name
  return &V;
}
'''


def main() -> int:
    clang = sw._find_clang(None)
    if clang is None:
        print("symexec_reach_sweep_fixture: needs clang++, skipped")
        return 0

    # 1) THE EXTRACTOR selects fold-shaped DEFINITIONS: a rewrite-returning function with a body.
    #    A declaration is not a definition, a bool/void helper has no rewrite to discharge, and a
    #    brace inside a string or a comment does not end a body.
    folds = {f["name"]: f for f in sw.extract_folds(SAMPLE)}
    assert set(folds) == {"foldThatCompiles", "foldNeedingVocabulary", "foldNeedingAPassLocalHelper",
                          "foldDefinedAsAMethod", "helperUsedByAFold"}, sorted(folds)
    assert folds["foldDefinedAsAMethod"]["cls"] == "InstCombinerImpl", folds["foldDefinedAsAMethod"]
    assert folds["foldThatCompiles"]["src"].rstrip().endswith("}"), "a body must end at its OWN brace"
    assert "helperUsedByAFold" in folds["foldNeedingAPassLocalHelper"]["src"]

    # 2) THE CLASSIFIER decides pass-local by LOOKING, not by the shape of the name: a helper defined
    #    in this file is the pass's own, and no naming convention distinguishes it from a missing one.
    local = sw.local_definitions(SAMPLE)
    assert "helperUsedByAFold" in local, local
    assert sw._classify("helperUsedByAFold", local) == "pass-local"
    assert sw._classify("helperUsedByAFold", set()) == "other", \
        "without the file's own definitions the same name is indistinguishable from missing vocabulary"
    assert sw._classify("computeKnownBits", local) == "knownbits"
    assert sw._classify("m_Xor", local) == "matcher"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 3) COMPILING IS THE MEASUREMENT. A fold in the shim's vocabulary compiles; one naming
        #    something the shim lacks does not, and the missing name is reported.
        ok = sw.probe(folds["foldThatCompiles"], clang, tmp)
        assert ok["compiles"] and not ok["blockers"], ok
        miss = sw.probe(folds["foldNeedingVocabulary"], clang, tmp)
        assert not miss["compiles"] and "computeKnownBits" in miss["blockers"], miss
        assert sw._classify(miss["blockers"][0], local) == "knownbits", miss
        assert not miss["shape_mismatch"], miss

        # 4) A METHOD is put back in a class deriving the shim's pass object, so `Builder` and the
        #    rest resolve as members exactly as they do upstream -- otherwise every member fold in
        #    the file would be miscounted as blocked.
        meth = sw.probe(folds["foldDefinedAsAMethod"], clang, tmp)
        assert meth["compiles"], ("a member fold must be probed as a member", meth)

        # 5) THE ACCOUNTING TEETH. Errors that name nothing must not read as an empty blocker list.
        shape = sw.probe(sw.extract_folds(SHAPE_ONLY)[0], clang, tmp)
        assert not shape["compiles"] and not shape["blockers"] and shape["errors"] > 0, shape
        assert shape["shape_mismatch"], \
            ("a fold blocked only by API SHAPE must be marked as such -- reported as zero blockers "
             "it sorts to the top of the work list as the easiest fold in the file, which is the "
             "opposite of true", shape)

    print("symexec_reach_sweep_fixture OK: the reach measurement is reproducible from source in the "
          "tree -- the extractor takes fold-shaped definitions only (declarations, bool/void helpers "
          "and braces inside strings and comments do not fool it), member folds are probed as "
          "members rather than counted as blocked, pass-local helpers are decided by looking at the "
          "file's own definitions rather than guessed from the name, and a fold whose errors name "
          "nothing is reported as a SHAPE mismatch instead of as a fold with nothing missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
