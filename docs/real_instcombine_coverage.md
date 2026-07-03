# Coverage scan: O2T's miner on real LLVM InstCombine

A first measurement of O2T's pattern vocabulary against **real** LLVM source, run with the
text miner (`tools/cv-mine-pass-source.py`, regex-based — no compilation needed) over the
checkout at `llvm/lib/Transforms/InstCombine/*.cpp`.

## What was scanned

| Quantity | Count |
| --- | --- |
| InstCombine `.cpp` files | 15 |
| Total source lines | 49,788 |
| `m_*` matcher uses (PatternMatch idioms) | 5,681 |
| `replaceInstUsesWith(...)` fold sites | 430 |

## What the miner recognized

The text miner located **1,285 matcher-idiom sites** that map to a known O2T marker,
spanning **23 fold families** across **8 predicate kinds** (matcher 432, equality 240,
vector 189, loop 167, cfg 139, memory 93, terminator 20, legality 5). Largest families:
`and-self` (228), `induction-phi` (164), `add-zero` (158), `branch-chain` (137),
`mul-one` (90), `or-zero` (84). Densest files: `InstCombineCompares.cpp` (229),
`InstCombineAndOrXor.cpp` (178), `InstCombineCalls.cpp` (158).

## Honest reading (no silent caps)

- These are **matcher-token occurrences** (e.g. an `m_And(` appearing), a **recall-oriented**
  signal of where O2T's idiom vocabulary shows up in real code — **not** verified folds.
  None carry a recovered rewrite, so precision for "a fold O2T can actually discharge" is
  low and unmeasured here.
- The **precise** metric — of the ~430 real fold sites, how many O2T recognizes *and proves*
  — requires the **AST miner** (`cv-mine-pass-source-ast`, LibTooling), which needs a
  compilation database, i.e. a configured/built LLVM (blocked locally by disk). Until then
  this scan is a lower bound on *reach*, not a coverage claim.
- Takeaway: O2T's matcher/idiom vocabulary is pervasive in real InstCombine (~1.3k sites
  over 5.7k matcher uses), which is encouraging for the source-mining direction; turning
  that into a verified-fold coverage percentage is the next step once an LLVM build is
  available.
