#!/usr/bin/env python3
"""Measure what stands between the symbolic-LLVM shim and REAL InstCombine folds.

The symexec track verifies upstream folds by compiling their unmodified C++ against
`o2t/symexec/symbolic_llvm.h` and executing them. Reach is therefore bounded by one blunt fact: does
the fold's real source COMPILE against the shim at all. That number ("N of M fold-shaped functions")
has been quoted in fixture docstrings and the paper for several sessions, but the measurement behind
it was run by hand and never checked in -- so it could not be reproduced, and every later claim about
"what to model next" rested on a number nobody could re-derive. This tool is that measurement.

WHAT IT DOES. For each fold-shaped function in a real InstCombine `.cpp`, synthesize a translation
unit -- the shim header, the function verbatim, an empty `main` -- and run `clang++ -fsyntax-only`.
Compiling is necessary, not sufficient: a fold that compiles still has to EXECUTE and REWRITE before
anything can be discharged (two vendored folds once compiled, ran, and silently never rewrote). So
this reports an UPPER BOUND on reach and says so.

WHAT IT IS FOR, and the trap it is built to avoid. The obvious use -- rank the missing identifiers by
frequency, implement the top of the list -- is exactly what has been MEASURED NOT TO WORK on this
track, three separate times: batches adding matcher vocabulary, generic construction, and the whole
`Intrinsic` surface (the single largest blocker at 68 occurrences) each unblocked ZERO further folds,
because a fold typically needs items from SEVERAL categories at once and clearing one category moves
nothing. The actionable output is therefore not the frequency table but `blocked_only_by`: the folds
whose ENTIRE remaining blocker set falls inside one category. Those are the folds a single batch can
actually reach. A category with a big frequency count and an empty `blocked_only_by` is a trap.

Blockers are classified by where they come from, and `pass-local` is decided by looking: an
identifier defined in the same source file is a pass-local helper, not shim vocabulary.

    python3 tools/cv-symexec-reach-sweep.py --src-dir ~/.cache/o2t/llvm18-instcombine \
        --report /tmp/reach.json [--category knownbits] [--jobs 8]

Reproduce the sources (llvmorg-18.1.8):
    curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/<file>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER_DIR = ROOT / "o2t" / "symexec"

# A fold-shaped function: returns a Value/Instruction (the InstCombine rewrite contract -- a
# replacement value or null to decline), and has a body. Anything else in these files is
# infrastructure. `bool` helpers and `void` visitors are deliberately NOT counted: they have no
# rewrite to discharge, so calling them "reachable" would inflate the denominator with functions the
# track could never verify.
_FOLD_RE = re.compile(
    r"^(?:static\s+)?(?:const\s+)?(?:Value|Instruction|BinaryOperator|ICmpInst|CmpInst)\s*\*\s*"
    r"(?:(InstCombinerImpl|InstCombiner)::)?([A-Za-z_]\w*)\s*\(",
    re.M)

# Any function definition in the file, used ONLY to decide whether an unknown identifier is a
# pass-local helper (defined right there) rather than missing shim vocabulary.
_ANYDEF_RE = re.compile(r"^[A-Za-z_][\w:<>,\s*&]*?\b(?:(\w+)::)?(\w+)\s*\([^;]*?\)\s*(?:const\s*)?\{", re.M)

_DIAG_RE = [
    re.compile(r"use of undeclared identifier '([^']+)'"),
    re.compile(r"unknown type name '([^']+)'"),
    re.compile(r"no type named '([^']+)'"),
    re.compile(r"no member named '([^']+)'"),
    re.compile(r"no matching function for call to '([^']+)'"),
    re.compile(r"use of undeclared identifier '([^']+)'"),
    re.compile(r"'([^']+)' does not refer to a value"),
    re.compile(r"no template named '([^']+)'"),
]

# Where a missing name comes from. Order matters: first match wins.
_CATEGORIES = [
    ("knownbits", re.compile(r"^(KnownBits|computeKnownBits|MaskedValueIsZero|isKnownNonZero|"
                             r"isKnownNonNegative|isKnownNegative|isKnownPositive|ComputeNumSignBits|"
                             r"computeKnownBitsFromContext|Known|LHSKnown|RHSKnown|isKnownToBeAPowerOfTwo)$")),
    ("constant-range", re.compile(r"^(ConstantRange|computeConstantRange|getConstantRange)$")),
    ("apint", re.compile(r"^(APInt|APSInt|getSignMask|getSignedMinValue|getSignedMaxValue|"
                         r"getOneBitSet|getLowBitsSet|getHighBitsSet|countLeadingZeros|"
                         r"countTrailingZeros|logBase2|getLimitedValue|zextOrTrunc|sextOrTrunc)$")),
    ("intrinsic", re.compile(r"^(Intrinsic|IntrinsicInst|II|WithOverflowInst|SaturatingInst|"
                             r"getIntrinsicID|MinMaxIntrinsic)$")),
    ("simplify-query", re.compile(r"^(SQ|SimplifyQuery|simplify\w+Inst|Simplify\w+)$")),
    ("constant-fold", re.compile(r"^(ConstantExpr|ConstantFoldBinaryOpOperands|ConstantAggregateZero|"
                                 r"ConstantDataVector|ConstantVector|UndefValue|PoisonValue|Constant)$")),
    ("type-infra", re.compile(r"^(VectorType|IntegerType|FixedVectorType|ScalableVectorType|PointerType|"
                              r"StructType|ArrayType|DataLayout|DL|getScalarSizeInBits|Type)$")),
    ("matcher", re.compile(r"^m_\w+$")),
    ("builder-ir", re.compile(r"^(IRBuilderBase|InsertPointGuard|SelectInst|PHINode|GetElementPtrInst|"
                              r"LoadInst|StoreInst|CastInst|TruncInst|FreezeInst|CallInst|"
                              r"BasicBlock|Function|Module|Use|User)$")),
]


def _classify(name: str, local_defs: set[str]) -> str:
    # A name DEFINED IN THIS FILE is a pass-local helper. Decided by looking rather than by
    # pattern-matching the name, because the two are indistinguishable by spelling.
    if name in local_defs:
        return "pass-local"
    for cat, rx in _CATEGORIES:
        if rx.match(name):
            return cat
    return "other"


def _body_end(text: str, open_brace: int) -> int:
    """Index just past the matching `}`. Brace counting, skipping strings/chars/comments."""
    depth, i, n = 0, open_brace, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i = text.find("\n", i)
            if i < 0:
                return -1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                return -1
            i = j + 2
            continue
        if c in "\"'":
            q, i = c, i + 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def extract_folds(text: str) -> list[dict]:
    """Fold-shaped function definitions, verbatim, with their class qualifier if any."""
    out, seen = [], set()
    for m in _FOLD_RE.finditer(text):
        cls, name = m.group(1), m.group(2)
        brace = text.find("{", m.end())
        semi = text.find(";", m.end())
        if brace < 0 or (0 <= semi < brace):          # a declaration, not a definition
            continue
        end = _body_end(text, brace)
        if end < 0:
            continue
        key = (cls, name, m.start())
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "cls": cls, "src": text[m.start():end]})
    return out


def local_definitions(text: str) -> set[str]:
    return {m.group(2) for m in _ANYDEF_RE.finditer(text)}


def probe(fold: dict, clang: str, tmp: Path) -> dict:
    """Compile ONE fold against the shim; report whether it compiles and what blocked it."""
    src = fold["src"]
    if fold["cls"]:
        # A member fold is put back in a class deriving the shim's pass object, so `Builder`,
        # `replaceInstUsesWith` and friends resolve as members exactly as they do upstream.
        src = re.sub(r"\b(InstCombinerImpl|InstCombiner)::", "", src, count=1)
        src = "struct CVProbe : InstCombinerImpl {\n" + src + "\n};\n"
    tu = tmp / f"{fold['cls'] or 'free'}_{fold['name']}.cpp"
    tu.write_text('#include "symbolic_llvm.h"\n\n' + src + "\n\nint main() { return 0; }\n")
    r = subprocess.run([clang, "-std=c++17", "-fsyntax-only", "-ferror-limit=0",
                        "-I", str(HEADER_DIR), str(tu)], capture_output=True, text=True)
    if r.returncode == 0:
        return {"name": fold["name"], "cls": fold["cls"], "compiles": True, "blockers": []}
    names: set[str] = set()
    for line in r.stderr.splitlines():
        if ": error:" not in line:
            continue
        for rx in _DIAG_RE:
            mm = rx.search(line)
            if mm:
                names.add(mm.group(1))
                break
    errors = sum(1 for line in r.stderr.splitlines() if ": error:" in line)
    # A fold whose errors NAME NOTHING is not a fold with nothing to do -- it is a fold the shim
    # already has the vocabulary for and gets the SHAPE wrong: `Value` returned by value where
    # upstream binds `Value *`, an APInt missing an arithmetic operator, an arity that disagrees.
    # Reported as an empty blocker list, those read as the easiest folds in the file when they are a
    # different kind of work entirely, so the unnamed errors are counted rather than dropped.
    return {"name": fold["name"], "cls": fold["cls"], "compiles": False,
            "blockers": sorted(names), "errors": errors,
            "shape_mismatch": errors > 0 and not names}


def sweep(files: list[Path], clang: str, jobs: int) -> dict:
    per_file, folds, local_defs = {}, [], set()
    for f in files:
        text = f.read_text()
        fs = extract_folds(text)
        local_defs |= local_definitions(text)
        for x in fs:
            x["file"] = f.name
        per_file[f.name] = len(fs)
        folds += fs

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            results = list(ex.map(lambda x: probe(x, clang, tmp), folds))
    for r, f in zip(results, folds):
        r["file"] = f["file"]
        r["categories"] = sorted({_classify(b, local_defs) for b in r["blockers"]})

    compiling = [r for r in results if r["compiles"]]
    blocked = [r for r in results if not r["compiles"]]

    freq: dict[str, int] = {}
    catfreq: dict[str, int] = {}
    for r in blocked:
        for b in r["blockers"]:
            freq[b] = freq.get(b, 0) + 1
            c = _classify(b, local_defs)
            catfreq[c] = catfreq.get(c, 0) + 1

    # THE ACTIONABLE VIEW: folds whose ENTIRE blocker set is one category, i.e. the ones a single
    # modelling batch could actually unblock. A category that is frequent but never appears alone
    # cannot be cleared on its own -- which is precisely why frequency ranking has failed here.
    only: dict[str, list] = {}
    for r in blocked:
        if r.get("shape_mismatch"):
            only.setdefault("shape-mismatch", []).append(
                {"file": r["file"], "name": r["name"], "blockers": [], "errors": r["errors"]})
        elif len(r["categories"]) == 1:
            only.setdefault(r["categories"][0], []).append(
                {"file": r["file"], "name": r["name"], "blockers": r["blockers"]})

    return {
        "files": per_file,
        "fold_shaped": len(folds),
        "compiles": len(compiling),
        "blocked": len(blocked),
        "compiling_folds": [{"file": r["file"], "name": r["name"]} for r in compiling],
        "blocker_frequency": dict(sorted(freq.items(), key=lambda kv: -kv[1])),
        "category_frequency": dict(sorted(catfreq.items(), key=lambda kv: -kv[1])),
        "blocked_only_by": {k: v for k, v in sorted(only.items(), key=lambda kv: -len(kv[1]))},
        "caveat": ("compiling is an UPPER BOUND on reach: a fold that compiles must still execute, "
                   "rewrite, and be discharged before it counts as verified"),
    }


def _find_clang(explicit: str | None) -> str | None:
    for cand in (explicit, "clang++", "/opt/homebrew/opt/llvm@18/bin/clang++", "/usr/bin/clang++"):
        if not cand:
            continue
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src-dir", type=Path, required=True, help="a directory of real InstCombine .cpp files")
    ap.add_argument("--files", nargs="*", help="restrict to these file names")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--category", help="print the folds blocked ONLY by this category")
    ap.add_argument("--clang")
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args(argv)

    clang = _find_clang(args.clang)
    if clang is None:
        print("cv-symexec-reach-sweep: no clang++ found", file=sys.stderr)
        return 2
    files = sorted(args.src_dir.glob("*.cpp"))
    if args.files:
        files = [f for f in files if f.name in set(args.files)]
    if not files:
        print(f"cv-symexec-reach-sweep: no .cpp files under {args.src_dir}", file=sys.stderr)
        return 2

    rep = sweep(files, clang, args.jobs)
    if args.report:
        args.report.write_text(json.dumps(rep, indent=2) + "\n")

    print(f"fold-shaped: {rep['fold_shaped']}   compiles: {rep['compiles']}   blocked: {rep['blocked']}")
    print("\ncategory frequency (NOT a work plan -- see blocked_only_by):")
    for c, n in rep["category_frequency"].items():
        print(f"  {c:16s} {n}")
    print("\nfolds blocked by exactly ONE category (what a single batch could unblock):")
    for c, v in rep["blocked_only_by"].items():
        print(f"  {c:16s} {len(v)}")
    if args.category:
        print(f"\n-- blocked only by {args.category} --")
        for f in rep["blocked_only_by"].get(args.category, []):
            print(f"  {f['file']:32s} {f['name']:44s} {', '.join(f['blockers'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
