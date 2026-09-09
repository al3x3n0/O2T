# Real third-party pass verification: LLVM-Tutor MBAAdd

Experiment date: 2026-09-08. O2T checkout: `229b9ea`.

The unchanged third-party plugin passed the checks applied in this experiment. No upstream
defect was found. This establishes results for the tested input functions and modeled semantics;
it is not a proof of the pass for every possible input module.

## Target and reproducibility

- Repository: [banach-space/llvm-tutor](https://github.com/banach-space/llvm-tutor).
- Revision: `5ad07189e9322a83f324323c5aa3c1ba0dc6ee53` (LLVM 18 update, 2024-04-27).
- Source: [lib/MBAAdd.cpp at the pinned revision](https://github.com/banach-space/llvm-tutor/blob/5ad07189e9322a83f324323c5aa3c1ba0dc6ee53/lib/MBAAdd.cpp).
- Installed toolchain: LLVM 18.1.8. The upstream checkout remained clean.
- Actual plugin: `build/mbaadd-verification-20260908/libMBAAdd.dylib`.
- Artifacts: [experiment directory](../build/mbaadd-verification-20260908/).
- [Provenance and artifact hashes](../build/mbaadd-verification-20260908/provenance.json).

LLVM-Tutor is an external educational project, not a production compiler vendor. Its MBAAdd
plugin rewrites scalar 8-bit addition using mixed Boolean/arithmetic operations. It was selected
because it can be built unchanged against the installed LLVM and has upstream transformation tests.
The plugin was loaded through `opt -load-pass-plugin ... -passes=mba-add`; no built-in pass stood
in for it. `opt -verify-each` accepted its output.

The plugin was built directly from its original source and header:

```sh
git clone https://github.com/banach-space/llvm-tutor.git build/third-party-llvm-tutor
git -C build/third-party-llvm-tutor checkout --detach 5ad07189e9322a83f324323c5aa3c1ba0dc6ee53
mkdir -p build/mbaadd-verification-20260908
/opt/homebrew/opt/llvm@18/bin/clang++ -std=c++17 -fPIC -shared -fno-rtti \
  -I/opt/homebrew/opt/llvm@18/include -Ibuild/third-party-llvm-tutor/include \
  build/third-party-llvm-tutor/lib/MBAAdd.cpp -Wl,-undefined,dynamic_lookup \
  -o build/mbaadd-verification-20260908/libMBAAdd.dylib
```

The two upstream `MBA_add_8bit.ll` and `MBA_add_32bit.ll` FileCheck tests passed. The former also
received O2T translation validation with cross-checks. The upstream executable test was not run.

## Verification results

| Input set | Functions | O2T proved | O2T refuted | O2T unsupported | Actually changed |
| --- | ---: | ---: | ---: | ---: | ---: |
| Upstream 8-bit IR test | 1 | 1 | 0 | 0 | 1 |
| Additional corpus | 12 | 11 | 0 | 1 | 10 |
| Combined | 13 | 12 | 0 | 1 | 11 |

Two additional functions deliberately exercise cases the pass must leave unchanged: i32 addition
and vector addition. They are controls, not extra transformation coverage. Thus 10 changed functions
were proved by O2T; the eleventh changed function was the declined loop.

The additional corpus covers plain addition, signed/unsigned/both overflow flags, repeated operands,
a constant operand, `freeze`, branches/phi, pointer memory, and a counted loop, plus the controls.
All 11 O2T proofs in that corpus were non-vacuous and agreed with Bitwuzla. Direct calls to reference
Alive2 returned `proved` for all 12 pairs, with zero non-answers. Alive2's loop result is reported
as its tool verdict, not used here as an unbounded-loop correctness claim. Six suitable functions
also agreed under `lli` on 48 samples each; those samples are execution corroboration, not proofs.

- [Corpus and per-function O2T results](../build/mbaadd-verification-20260908/corpus-tv.json).
- [Actual transformed IR](../build/mbaadd-verification-20260908/transformed.ll).
- [Independent per-oracle answers](../build/mbaadd-verification-20260908/independent-oracles.json).

Use the direct plugin-TV route to reproduce the main check:

```sh
python3 tools/cv-validate-scalar-tv.py --passes mba-add \
  --pass-plugin build/mbaadd-verification-20260908/libMBAAdd.dylib \
  --source build/mbaadd-verification-20260908/corpus.ll \
  --opt-bin /opt/homebrew/opt/llvm@18/bin/opt \
  --report build/mbaadd-verification-20260908/corpus-tv.json
python3 build/mbaadd-verification-20260908/check_oracles.py
```

## What O2T could not establish directly

Source mining (`cv-extract-pass-model.py --mine ... --symexec`) recovered **zero fold functions**.
This implementation uses instruction traversal, guards, nested builders and in-place replacement;
the source-recovery result says nothing about the pass's correctness.

For `sum_loop`, whole-function TV declined `cyclic CFG (loop) -- not modeled`. Trying the separate
loop-induction implementation on the same actual before/after IR also declined: `operand '%limit'`.
The missing end-to-end agent plugin/corpus CLI wiring and headline-level residue selection were
not changed. Instead, a small experiment driver explicitly handed this measured residual function
to the existing `run_pass_agent` and `synthesize-tool` machinery. This is a focused handoff, not
evidence that the agent autonomously selected or routed the gap.

## DeepSeek-generated supplemental check

DeepSeek V4 Flash generated a Python `ctypes` checker that calls the actual compiled `sum_loop`
functions before and after the plugin. The libraries came from LLVM-extracted function IR compiled
with `clang -O0`; the generated checker did not substitute a Python model for the pass output.

The function takes two `noundef` uint8 inputs, a value and a trip count. All **65,536 input pairs**
were executed, covering trip counts 0 through 255. Before and after agreed everywhere. This is
exhaustive native value evidence for this finite function domain, with the native compiler/runtime
in the trust chain. It is not a general LLVM poison/UB proof or a source-wide pass proof.

A separate mutant plugin changed the final additive constant from 111 to 112. The upstream checkout
was not modified. O2T refuted nine changed acyclic functions in this negative control, declined the
loop, and proved the two unchanged controls. The generated loop checker found **65,280 mismatches**;
its first witness was `x=0, n=1`, returning 0 before and 1 after.

An independent exhaustive comparison checked all three native libraries against their expected
modular products: `x*n mod 256` for before/after, and `(x+1)*n mod 256` for the planted mutant.
All **196,608 value comparisons** agreed.

### Agent failures observed, and review performed

1. The first generated fixture tested an embedded copy of the checker instead of the staged file.
   Replacing the staged file with `raise SystemExit(42)` still left that fixture passing.
2. One additional DeepSeek call proposed a corrected fixture, but the response ended with an extra
   closing brace. The normal transport rejected it as malformed JSON and executed no repair action.
3. After inspecting the candidate code, the reviewer extracted the valid action and replayed it in
   a separate `reviewed-repair` staging area. This was explicit intervention; the production parser
   was not relaxed or changed. The reviewed fixture passed both actual libraries and the mutant,
   replayed the witness, and failed when the staged checker was broken.

The original generated checker was also executed directly and independently checked. Its runtime
result was correct even though its first fixture was poorly bound. The model's prose conclusion
said the negative control had “one mismatch”; the actual JSON reports 65,280. Results above use
measured artifacts rather than the model's conclusion.

Three live API calls used **23,009 tokens**. The original agent concluded `needs-human`; the single
repair attempt hit its configured step cap after the malformed response. No staged result became
formal evidence, and no generated tool was promoted into `tools/` or registered as a trusted check.
The API key was kept in process memory; experiment text artifacts were scanned for key-like values
and contained none.

- [Original agent report](../build/mbaadd-verification-20260908/agent-report.json).
- [Rejected repair report](../build/mbaadd-verification-20260908/agent-repair-report.json).
- [Reviewed checker](../build/mbaadd-verification-20260908/reviewed-repair/cv-agent-mbaadd-loop-check/cv-agent-mbaadd-loop-check.py).
- [Reviewed fixture, mutation sensitivity, and execution results](../build/mbaadd-verification-20260908/reviewed-repair-results.json).
- [Independent native reference comparison](../build/mbaadd-verification-20260908/loop-independent-reference.json).

## Implications for the verification process

Actual-plugin translation validation worked beyond source recovery, including overflow flags,
memory and branches. A generated, source-bound execution check added useful evidence for a formal
decline. Its usefulness depended on reviewing the fixture, preserving the scope of its conclusion,
and checking a planted failure. The experiment supports integrating candidate checks behind those
gates; it does not support automatic promotion based on a self-authored fixture passing.
