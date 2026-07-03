#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PYTHON_BIN=${PYTHON3:-python3}
PASS_CONSTRAINTS="$ROOT_DIR/constraints/pass_constraints.json"
SEMANTIC_FACTS="$ROOT_DIR/constraints/semantic_facts.json"
OPTIMIZATION_INTENTS="$ROOT_DIR/constraints/optimization_intents.json"
GUARD_SEMANTICS="$ROOT_DIR/constraints/guard_semantics.json"

echo "json syntax validation:"
for registry in "$PASS_CONSTRAINTS" "$SEMANTIC_FACTS" "$OPTIMIZATION_INTENTS" "$GUARD_SEMANTICS"; do
    "$PYTHON_BIN" -m json.tool "$registry" >/dev/null
    echo "  ok: ${registry#$ROOT_DIR/}"
done

echo "semantic facts validation:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-validate-semantic-facts.py" \
    --semantic-facts "$SEMANTIC_FACTS" \
    --pass-constraints "$PASS_CONSTRAINTS" \
    --optimization-intents "$OPTIMIZATION_INTENTS"

echo "guard semantics validation:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-validate-guard-semantics.py" \
    --guard-semantics "$GUARD_SEMANTICS"

if [ "${CV_SKIP_Z3:-0}" = "1" ]; then
    echo "formal registry validation: skipped (CV_SKIP_Z3=1)"
    exit 0
fi

z3_bin=""
if [ "${Z3:-}" ]; then
    if [ ! -x "$Z3" ]; then
        echo "formal registry validation: Z3 is set but not executable: $Z3" >&2
        exit 1
    fi
    z3_bin=$Z3
else
    z3_bin=$(command -v z3 || true)
fi

if [ -z "$z3_bin" ]; then
    echo "formal registry validation: skipped (z3 not found)"
    exit 0
fi

if [ "${CHECK_REGISTRIES_OUT:-}" ]; then
    out_dir=$CHECK_REGISTRIES_OUT
    mkdir -p "$out_dir"
else
    out_dir=$(mktemp -d "${TMPDIR:-/tmp}/o2t-registry-checks.XXXXXX")
fi

echo "formal registry validation:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-validate-intent-registry.py" \
    --z3 "$z3_bin" \
    --intents "$OPTIMIZATION_INTENTS" \
    --out "$out_dir/intent-registry.jsonl" \
    --emit-smt "$out_dir/intent-registry-smt"
echo "formal registry validation: completed with $z3_bin"

echo "multi-width scalar proofs (incl. i1 + odd/large 4/17/128):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-multiwidth.py" \
    --z3 "$z3_bin" --widths 1,4,8,16,17,32,64,128 --require-all \
    --report "$out_dir/multiwidth.json"

echo "negative-intent + mutation soundness:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-check-negative-intents.py" \
    --z3 "$z3_bin" --mutate \
    --report "$out_dir/negative-intents.json"

echo "extended-identity families (reassociate/instsimplify/shift; i1..i128):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-identities.py" \
    --z3 "$z3_bin" --widths 1,4,8,16,17,32,64,128 --require-all \
    --report "$out_dir/extended-identities.json"

echo "mini-alive local translation validation (.ll -> SMT round-trip):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mini-alive.py" \
    --selftest --z3-bin "$z3_bin"

echo "intent->formal lifter grammar (shift/neg/not/select):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-lift-grammar-selftest.py" \
    --z3-bin "$z3_bin" --report "$out_dir/lift-grammar.json"

echo "poison-generating flag semantics (nsw/nuw/exact refinement):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-flags.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-flags.json"

echo "memory-transform soundness (DSE / store-load forwarding over SMT arrays):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-memory.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-memory.json"

echo "floating-point fast-math refinement (nnan/ninf as poison):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-fastmath.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-fastmath.json"

echo "beyond-peephole: multi-instruction GVN/CSE/reassociate:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-multi-instr.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-multi-instr.json"

echo "beyond-peephole: control-flow / SimplifyCFG (branch model):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-cfg.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-cfg.json"

echo "beyond-peephole: interprocedural / inlining:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-interproc.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-interproc.json"

echo "beyond-peephole: bounded loop transforms (unroll/LICM/unswitch):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-loop.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-loop.json"

echo "beyond-peephole: UNBOUNDED loops via 1-induction (LICM / strength-reduction):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-loop-induction.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-loop-induction.json"

echo "beyond-peephole: synthesize the inductive loop invariant (affine template):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-synth-invariant.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/synth-invariant.json"

echo "end-to-end: mine a loop from source -> synthesize invariant -> prove all n:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-loop-invariant.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-loop-invariant.json"

echo "richer recurrences: QUADRATIC invariant synthesis (over Z; e.g. acc+=i):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-synth-invariant-poly.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/synth-invariant-poly.json"

echo "richer recurrences: COUPLED multi-accumulator invariants (sum-of-sums):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-synth-invariant-coupled.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/synth-invariant-coupled.json"

echo "two-loop RELATIONAL (simulation) synthesis -- prove a loop transform all n:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-synth-relational.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/synth-relational.json"

echo "end-to-end: mine a loop transform (before/after) from source -> prove all n:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-relational.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-relational.json"

echo "auto-discover the output pairing of a two-loop transform (permuted/renamed):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-synth-pairing.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/synth-pairing.json"

echo "parse REAL LLVM IR loops (PHI recurrence) -> synthesize closed form for all n:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-llvm-loop.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-llvm-loop.json"

echo "prove a loop TRANSFORM from REAL LLVM IR (before/after .ll) for all n:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-llvm-relational.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-llvm-relational.json"

echo "memory-delta loop transforms via uninterpreted loads (LICM/GVN of a load):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-memory-loop.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-memory-loop.json"

echo "conditional (ite-stride closed form) + geometric (relational) recurrences:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-prove-cond-geom-loop.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/prove-cond-geom-loop.json"

echo "infer loop-transform intent from pass SOURCE (SCEV idioms) + prove it:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-pass-scev.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-pass-scev.json"

opt_bin=$(command -v opt || true)
if [ -z "$opt_bin" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then
    opt_bin=/opt/homebrew/opt/llvm@18/bin/opt
fi
if [ -n "$opt_bin" ]; then
    echo "prove .ll loop transform via SCEV frontend (rotated multi-block, no regex IR parse):"
    "$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-scev-relational.py" \
        --selftest --z3-bin "$z3_bin" --opt-bin "$opt_bin" \
        --report "$out_dir/mine-scev-relational.json"

    echo "closed-loop translation validation (prove a REAL opt pass's output == its input):"
    "$PYTHON_BIN" "$ROOT_DIR/tools/cv-translation-validate.py" \
        --selftest --z3-bin "$z3_bin" --opt-bin "$opt_bin" \
        --report "$out_dir/translation-validate.json"
else
    echo "skipping SCEV .ll frontend check (opt/LLVM not found)"
fi

clang_bin=$(command -v clang || true)
if [ -z "$clang_bin" ] && [ -x /opt/homebrew/opt/llvm@18/bin/clang ]; then
    clang_bin=/opt/homebrew/opt/llvm@18/bin/clang
fi
if [ -n "$clang_bin" ]; then
    echo "infer pass-source intent via REAL Clang AST (no regex) + prove it:"
    "$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-clang-pass.py" \
        --selftest --z3-bin "$z3_bin" --clang-bin "$clang_bin" \
        --report "$out_dir/mine-clang-pass.json"
else
    echo "skipping Clang-AST pass-source check (clang not found)"
fi

if [ -x "$ROOT_DIR/build-clang-tools/cv-mine-pass-source-ast" ]; then
    echo "mine non-local (select/if-conversion) folds from real source -> prove:"
    "$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-nonlocal.py" \
        --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-nonlocal.json"
fi

echo "mine branch SHAPES (nested if/else -> ite-tree) -> prove CFG transform:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-mine-shapes.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/mine-shapes.json"

echo "second-solver cross-check (differential proving: z3 vs bitwuzla):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-cross-solver.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/cross-solver.json"

echo "concrete differential (lifted before/after vs random eval + real opt):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-differential-lift.py" \
    --selftest --report "$out_dir/differential-lift.json"

echo "born-proven candidates (registry cross-check via candidate path):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-verify-candidates.py" \
    --registry "$OPTIMIZATION_INTENTS" --z3-bin "$z3_bin" \
    --report "$out_dir/born-proven.json"

echo "relational guard lifting (rel assumptions):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-guard-lift-selftest.py" \
    --z3-bin "$z3_bin" --report "$out_dir/guard-lift.json"

echo "CEGIS guard inference (refutation -> precondition):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-infer-guard.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/guard-inference.json"

echo "declarative lift rules (parameterized templates):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-lift-rules.py" \
    --z3-bin "$z3_bin" --report "$out_dir/lift-rules.json"

echo "matcher-AST lifting (before-tree + whole transforms from PatternMatch/Builder):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-lift-matcher.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/lift-matcher.json"

echo "autonomous whole-transform lift from real findings:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-lift-finding.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/lift-finding.json"

echo "cross-validation (proof <-> real-opt translation validation):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-cross-validate.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/cross-validate.json"

echo "per-pass verification dossier:"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-pass-dossier.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/dossier.json"

echo "refutation triage (lifter-issue / precondition / miscompile):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-triage.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/triage.json"

echo "verify-pass driver + self-improve loop (promote verified -> rules, converge):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-verify-pass.py" \
    --selftest --loop --z3-bin "$z3_bin" \
    --promote "$out_dir/promoted-rules.json" --report "$out_dir/verify-pass.json"

echo "pass-model code lift (per-branch guard->sound; insufficient-guard hunt):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-lift-pass-model.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/pass-model.json"

echo "symbolic execution of fold (cascade path model; dead-branch + reachable miscompile):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-symexec-pass.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/symexec.json"

echo "miner-side branch extraction (findings -> per-function pass model -> symexec):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-extract-pass-model.py" \
    --selftest --z3-bin "$z3_bin" --report "$out_dir/extract-pass-model.json"

echo "KLEE-on-bitcode symbolic-IR fold harness (real KLEE if present, else native):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-klee-fold.py" \
    --selftest --report "$out_dir/klee-fold.json"

if command -v alive-tv >/dev/null 2>&1; then
    echo "real Alive2 translation validation (sound proved + unsound refuted):"
    "$PYTHON_BIN" "$ROOT_DIR/tools/cv-alive2-check-ir.py" \
        --selftest --out "$out_dir/alive2.json"
fi

echo "generate fold harness FROM a real mined fold (model -> compilable harness):"
"$PYTHON_BIN" "$ROOT_DIR/tools/cv-gen-fold-harness.py" \
    --selftest --report "$out_dir/gen-fold-harness.json"
