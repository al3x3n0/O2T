# Symbolic execution of real pass sources

> Handoff / catch-up doc for the `o2t/symexec/` subsystem.
> Audience: an engineer or agent (e.g. OpenAI Codex) picking up this work cold.

## Why this exists (the mission)

O2T's goal is to **verify in-house / third-party optimizer *sources* that use the LLVM API** —
custom `InstCombine`-style passes a team writes against `PatternMatch` + `IRBuilder`. Alive2 can't
reach these: it checks IR *pairs* produced by passes already wired into `opt`, not arbitrary
source. LLVM's own passes are a convenient **test-bed / oracle** here, not the target.

The rest of O2T recovers a pass's *intent* from its source (regex/AST miners) and discharges that
model — **model-verification**. This subsystem goes one level deeper to **implementation-verification**:
it compiles and symbolically executes the **actual C++** of a fold over a symbolic input, enumerates
the fold's **real control-flow paths**, and on each rewriting path proves the rewrite **refines** the
input under exactly the facts that path's branches established. A pass that rewrites on a path whose
established facts are insufficient (an under-guarded pass) is **refuted with a concrete witness** —
caught from the genuine branch, not a pattern guess.

## The core pieces

| File | Role |
| --- | --- |
| `o2t/symexec/symbolic_llvm.h` | A "symbolic LLVM" **shim** (~330 lines). `Value` is an SMT term; `IRBuilder` build-calls return terms; `PatternMatch` matchers are real (recursive); analysis queries (`isKnownToBeAPowerOfTwo`, `isMustAlias`, …) are **choice points**. A real fold's C++ is written/compiled against this. |
| `o2t/symexec/real_pass.py` | The **driver** (~116 lines). Compiles a harness, `explore()`s its paths (enumerate query-outcome assignments), and `discharge_path()`s the **refinement obligation** to z3. |
| `o2t/symexec/klee_driver.py` (+ `klee_fold.c`) | Optional **KLEE** backend: replaces `{0,1}^k` enumeration with true symbolic branching (forks on feasible guards *and* input opcode). Graceful skip when KLEE absent; enumeration is the fallback. |
| `o2t/symexec/modelcheck.py` (+ `modelcheck_llvm.h`) | Optional **CBMC/ESBMC** backend: compiles a model-checker-friendly fold harness with nondet bitvector inputs and query outcomes, then asserts the same poison-aware refinement property. Graceful skip when no model checker is installed. |
| `o2t/symexec/modelcheck_intents.py` | Source-audit bridge: lowers validated scalar and CFG formal intent records to model-checker harnesses so deep audits can cross-check mined real-source rewrites. |
| `o2t/symexec/modelcheck_cfg.py` | Source-specific CFG bridge: mines SimplifyCFG diamond-to-select `CreateSelect` folds and modelchecks the recovered operand binding, so swapped select arms are surfaced as normal modelcheck findings. |
| `o2t/symexec/modelcheck_memory.py` | Source-specific memory bridge: mines DSE/store-forward op sequences plus alias guards and modelchecks the resulting finite-address memory obligation. |
| `o2t/symexec/modelcheck_licm.py` | Source-specific LICM bridge: mines hoist folds and modelchecks the recovered value-invariance / trap-safety obligation, so invariant-only trapping hoists are surfaced as normal modelcheck findings. |
| `o2t/symexec/modelcheck_globalopt.py` | Source-specific GlobalOpt bridge: mines dead-initializer defaulting folds and modelchecks whether the initializer can be observed after defaulting to zero. |
| `o2t/symexec/modelcheck_dce.py` | Source-specific DCE bridge: mines dead-instruction, dead-loop-instruction, and unused-alloca erasures and modelchecks whether the removed instruction/slot is unobservable, so unguarded erases are surfaced as normal modelcheck findings. |
| `o2t/symexec/modelcheck_slp.py` | Source-specific SLP bridge: mines reduction and binop-pack folds and modelchecks lane mapping plus reassociation obligations. |

Harness of real folds under test: `tests/fixtures/symexec_folds.cpp` (~481 lines).
Gating fixtures: `tests/fixtures/symexec_real_pass_fixture.py`, `tests/fixtures/klee_symexec_fixture.py`,
`tests/fixtures/modelcheck_real_pass_fake_fixture.py`, `tests/fixtures/modelcheck_real_pass_fixture.py`,
`tests/fixtures/modelcheck_generated_real_fixture.py`.
CLIs: `tools/cv-symexec-real-pass.py`, `tools/cv-klee-symexec-pass.py`,
`tools/cv-modelcheck-real-pass.py`, `tools/cv-modelcheck-cfg-pass.py`,
`tools/cv-modelcheck-memory-pass.py`, `tools/cv-modelcheck-dce-pass.py`.

## The core idea: per-path refinement

For each control-flow path of a fold, `discharge_path` builds one SMT query:

```
facts   := facts the taken branches established (from the v=1 queries) + path constraints
neg     := (and (not <input_poison>)
                (or (not (= <output> <input>)) <output_poison>))
query   := (set-logic <logic>) <decls> (assert facts...) (assert neg) (check-sat)
```

`unsat` ⇒ **proved** (the rewrite refines the input on this path); `sat` ⇒ **refuted**, and the
model is the witness. The obligation is **refinement, not value-equality**: where the input is
defined (`not input_poison`), the output must both equal it **and** be defined (`not output_poison`).
This is what catches bugs that are value-correct but introduce UB (see "poison" below).

`verify_fold(z3, exe, fold)` runs `explore` + `discharge_path` over every path and returns
`{paths, rewriting_paths, proved, refuted, ok, rows}`. `ok` ⇔ there is ≥1 rewriting path and none
refuted.

## What the shim models

**PatternMatch** (recursive, pool-allocated so nested `m_*(...)` stay valid): `m_Value`,
`m_Specific`, `m_ConstantInt`, `m_SpecificInt`, `m_Zero/m_One/m_AllOnes`, all binops
(`m_Add/Sub/Mul/And/Or/Xor/Shl/LShr/AShr/UDiv/SDiv/URem/SRem`), commutative `m_c_*`, `m_CombineOr`,
`m_OneUse`; plus `isa<>`/`dyn_cast<>`, `ConstantInt::get`.

**IRBuilder**: term-building `Create*` for every binop + `Select`, **plus poison-aware ops**
`CreateNSWAdd`, `CreateNUWAdd`, `CreateOrDisjoint`, `CreateExactUDiv`, `CreateFAddNNan`,
`CreateOrPoisoning`, `CreateFreeze`.

**Analysis queries** = choice points (each `cv_next_choice()` + records a decision/emits a
constraint when true). Current set and the SMT fact each establishes:

| Query | Establishes (when true) |
| --- | --- |
| `isKnownToBeAPowerOfTwo(P)` | `P` is a power of two |
| `isKnownNonZero / isKnownNonNegative / isKnownNegative(X)` | `X != 0` / `X >=s 0` / `X <s 0` |
| `willNotOverflowSignedAdd(X,Y)` | `(not saddo(X,Y))` |
| `willNotOverflowUnsignedAdd(X,Y)` | `(bvuge (X+Y) X)` |
| `haveNoCommonBitsSet(X,Y)` | `(X & Y) == 0` |
| `isKnownExactUDiv(X,Y)` | `(X urem Y) == 0` |
| `willNotBeNaN(X,Y)` | `(not (fp.isNaN (fp.add RNE X Y)))` |
| `isMustAlias(P,Q)` / `isNoAlias(P,Q)` | `P == Q` / `P != Q` |
| `isLowBitZero(X)` | `X` is even (bit 0 == 0) |
| `isInBounds(I,N)` | `(bvult I N)` |

## Poison / UB and three SMT theories

The refinement obligation carries per-value poison. Three structural ways a pass can be
value-correct yet unsound are covered:

- **Introduction** — a flag that adds poison: `nsw`/`nuw` (overflow), `or disjoint` (bit overlap),
  `udiv exact` (remainder), `fadd nnan` (NaN). Sound only if the matching safety query holds.
  Also the multi-instruction `add nsw (add nsw X,C1),C2 -> add nsw X,(C1+C2)` combine, whose **source
  is itself flagged** (`CV_INPUT_POISON` = the source's own poison): value-identical, but unsound when
  `C1+C2` overflows.
- **Contagion** — operand poison flowing through: `select C,true,Y -> or C,Y` is value-identical on
  i1 but poison when operand `Y` is, while the select is defined at `C=true`. The `freeze`-fixed
  variant proves. (The canonical reason `freeze` exists.)
- **UB** — out-of-bounds load speculation: hoisting `if (i<n) load a[i]` to an unconditional load is
  UB when `i>=n`; the OOB load carries poison `(bvuge i n)`.

The SMT logic is **per-path**, set by the fold via `cv_set_logic`:
`QF_BV` (integers, default) · `QF_FPBV` (floating-point / fast-math) · `QF_ABV` (arrays / memory).

## Bounded model-checking cross-check

`cv-modelcheck-real-pass.py` is an independent implementation-verification path for the scalar
subset. It does **not** reuse the SMT-string shim: `modelcheck_llvm.h` gives the model checker real
`uint32_t` values, `bool poison`, nondet inputs, and nondet analysis-query outcomes. When a query
returns true, the shim `assume`s the fact the real query establishes, then the harness asserts:

```
if (rewrote)
  input.poison || (!output.poison && output.bits == input.bits)
```

The v1 fixture covers three bug classes with guarded and planted-unsound variants: value
preconditions (`urem -> and`), poison introduction (`add -> add nsw`), and poison contagion
(`select -> or`, fixed with `freeze`). CBMC is preferred when both engines are installed; ESBMC is
the fallback. This backend is advisory and optional: absence of both engines is a skip, not a failed
proof, and the Z3/KLEE paths remain authoritative for richer theories.

Deep source audits can also opt into `--modelcheck-intents`. That path reads
`intent-validated.jsonl`, generates one harness per supported `scalar-bv32` or `cfg-bv32` formal
record, and also runs the CFG, memory, LICM, GlobalOpt, DCE, and SLP source adapters over selected pass sources. The final
merged artifact is `modelcheck-intents/modelcheck-summary.json`; component summaries live beside it
for scalar intent, CFG-source, memory-source, LICM-source, GlobalOpt-source, dce-source, and SLP-source runs. Unsupported domains are reported as
`unsupported`, not failed;
generated records carry the source marker/location, source function when known, and harness path,
with witness excerpts on refutation. Refuted/error records are also projected into compact
`findings` entries that the source audit, external wrapper, and orchestrator summaries can surface
directly. The audit remains advisory unless callers set budgets such as
`--max-modelcheck-refuted 0` or `--max-modelcheck-errors 0`. Baseline-aware gates
(`--max-new-modelcheck-refuted 0`, `--max-new-modelcheck-errors 0`) fail only on modelcheck findings
that are new relative to an audit baseline; baseline diffs preserve finding identity by source
function/function when available, report reason/domain changes as changed findings, and list top
new, resolved, and changed obligations in the text diff. Source-audit, external-wrapper, and
orchestrator text reports include an explicit `... N more` line when projected findings are
truncated.
The default width mode is `native`: a same-width bitvector record runs at its declared
`variable_bits` width (`1/8/16/32/64`, or `32` when omitted). For broader coverage, pass
`--modelcheck-widths 8,16,32,64` through source/external/orchestrator audits; this expands portable
constants with the existing multiwidth reencoder; source-mined memory uses the same finite-address
harness shape at the selected bit width. Non-portable widths are reported as
`unsupported` per width. Summaries preserve `width_mode`, `selected_widths`, per-width rollups, and
width-specific domains (`scalar-bv8`, `cfg-bv16`, `memory-bv16`, `loop-bv8`, `vector-bv8xN`); the
standalone modelcheck CLIs print status lines in the same `@<width>b <domain>` form used by audit
findings.

## Coverage (the fixture blocks)

`symexec_real_pass_fixture.py` is the source of truth; each block gates one capability:

| Block | What it proves / refutes |
| --- | --- |
| 1–3 | Guarded `urem`/`sdiv` prove; under-guarded `urem` refuted (0 facts) |
| 3b | Real-code idioms: nested patterns, `m_Specific`, constant matchers, `dyn_cast`, commutative `m_c_*` |
| 3c | Captured-constant / APInt: `mul X,C -> shl X,log2(C)`; unguarded refuted with non-pow2 witness |
| 3d | Poison **introduction** — `nsw`, `nuw`, `or disjoint`, `udiv exact`: guarded proved, unguarded refuted |
| 3e | Multi-instr **source-flagged** combine (`add nsw` of two nsw adds); refuted when `C1+C2` overflows |
| 3f | Poison **contagion** — `select->or` refuted, `or C, freeze Y` proved |
| 3g | **FP** fast-math `fadd nnan` (`QF_FPBV`) |
| 3h | **Memory** store-to-load forward under must-alias (`QF_ABV`) |
| 3i | Dead-store elimination under no-alias — same pattern, opposite guard |
| 3j | Multi-instruction + **known-bits** `mul (lshr X,1),2 -> X`; refuted with odd witness X=1 |
| 3k | **Whole worklist pass run** to fixpoint; composed output refined; planted `sub v,v->v` refuted |
| 3l | **Provenance / OOB** load speculation (`QF_ABV` + poison) |

## How to add a new fold (the recipe)

Adding a fold is a drop-in — **no `discharge_path` changes**:

1. **Shim** (`symbolic_llvm.h`): if the rewrite needs a precondition, add one `inline bool
   <query>(...)` that does `cv_next_choice()` and, when true, `cv_constraint("<smt fact>")`. If it
   introduces poison, add a `Create*` that sets `Value::poison`.
2. **Harness** (`symexec_folds.cpp`): write the fold the way real pass code is written (PatternMatch
   + query + IRBuilder). Add a `main` branch that builds the symbolic input tree (`cv_node`/literal
   Values), sets `input`, optionally `CV_INPUT_POISON`, `cv_decl(...)` for extra SMT declarations
   (i1 operands, Bool poison flags, arrays), and `cv_set_logic(...)` for FP/array theories, then
   `out = <fold>(...)`.
3. **Fixture** (`symexec_real_pass_fixture.py`): add a block asserting the guarded variant proves and
   the under-guarded variant is refuted with a witness.

The channels that make this driver-free: `cv_constraint` (path facts), `CV_INPUT_POISON` (source
poison), `cv_decl` (extra declarations), `cv_set_logic` (theory). The harness emits them all in the
per-path JSON; `discharge_path` consumes them generically.

## Running it

```sh
# the gating fixtures (need z3 + clang++; KLEE optional)
ctest --test-dir build -R 'symexec_real_pass_fixture|klee_symexec_fixture' --output-on-failure

# standalone CLI (verifies the sound folds, exits 0; JSON report on stdout)
python3 tools/cv-symexec-real-pass.py
python3 tools/cv-klee-symexec-pass.py        # KLEE path, skips cleanly if KLEE absent
python3 tools/cv-modelcheck-real-pass.py     # CBMC/ESBMC path, skips cleanly if absent
python3 tools/cv-modelcheck-intents.py --input intent-validated.jsonl --out-dir /tmp/mc-intents
python3 tools/cv-modelcheck-intents.py --input intent-validated.jsonl --out-dir /tmp/mc-intents --widths 8,16,32,64
python3 tools/cv-modelcheck-cfg-pass.py --source tests/fixtures/cfg_ifconv_folds.cpp --out-dir /tmp/mc-cfg
python3 tools/cv-modelcheck-memory-pass.py --source tests/fixtures/dse_memory_folds.cpp --out-dir /tmp/mc-memory
python3 tools/cv-modelcheck-licm-pass.py --source tests/fixtures/licm_hoist_folds.cpp --out-dir /tmp/mc-licm
python3 tools/cv-modelcheck-dce-pass.py --source tests/fixtures/dce_dead_instruction_folds.cpp --out-dir /tmp/mc-dce

# verify one fold by hand
python3 - <<'PY'
import shutil; from o2t.symexec import real_pass as R
z3, cxx = shutil.which("z3"), shutil.which("clang++")
exe = R.compile_harness("tests/fixtures/symexec_folds.cpp", clang=cxx)
print(R.verify_fold(z3, exe, "add_nsw_unguarded"))   # -> ok=False, refuted=1, with witness
PY
```

Toolchain: z3 at `/opt/homebrew/bin/z3`; clang++ via `clang++` on PATH (fallback
`/opt/homebrew/opt/llvm@18/bin/`). KLEE 3.2 at `/opt/homebrew/bin/klee` (links LLVM 16; emit bitcode
with `/opt/homebrew/opt/llvm@16/bin/clang`); `klee.h` at `/opt/homebrew/include/klee/`;
`libkleeRuntest.dylib` at `/opt/homebrew/lib/`.

> **Editor note:** clangd flags `symexec_folds.cpp` with "`symbolic_llvm.h` file not found" / cascade
> errors — it lacks the `-I o2t/symexec` include path. The real `clang++ -I ...` build (what
> `compile_harness` runs) is clean. Ignore the clangd diagnostics.

## Orchestrator wiring

Three implementation-verification strategies in the peephole family: `symexec-real-pass` (needs z3
+ clang++), `modelcheck-real-pass` (needs CBMC or ESBMC), and `klee-symexec` (needs z3 + klee).
`run.py`'s `resolve_context` exposes `klee` via `klee_driver.available()` and `model-checker` from
`cbmc`/`esbmc` on `PATH`. The `symexec-real-pass` handler lets the CLI default to `clang++` (the
resolved `ctx['clang']` is a C driver and can't link libc++).

## Honest scope / what's NOT covered

- Refinement is **per-path** over a single fold or a bounded straight-line block; no unbounded-size
  symbolic input.
- The CBMC/ESBMC backend is currently scalar integer + poison, source-mined CFG diamond/select
  bindings, and straight-line source-mined DSE/store-forward memory obligations; byte-granular DSE,
  CFG-shaped memory, atomics, FP, worklists, and richer mined third-party slices stay on the
  existing Z3/KLEE paths.
- Memory is a **flat array** (`QF_ABV`) with a bounds predicate — enough for store-to-load
  forwarding, DSE, and OOB speculation, but **not** full pointer provenance (alloca/lifetime/escape).
- The drop-in fold space (more flags, more peephole classes) is essentially saturated: each new one
  follows the same one-fold + one-query recipe. The genuinely-new frontiers are **architectural**:
  full provenance, unbounded inputs, and richer in-pass control flow.

See also: `docs/o2t-design.md` (the broader methods), `docs/real_instcombine_coverage.md` (matcher
vocabulary vs. real InstCombine). Project memory: `o2t-symexec-real-pass`, `o2t-mission`.
