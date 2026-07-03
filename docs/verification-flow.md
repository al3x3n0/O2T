# O2T Verification Flow

How an obligation travels from an input (a real LLVM pass, or generated IR) through a frontend, into
the parser-agnostic prover, out to a verdict, and — for every `proved` — through the
meta-verification trust base. This is the paper's Figure 2 (pipeline: source/IR → frontend →
recurrence/SMT → prover → {proved | witness}).

```mermaid
flowchart TD
  A["(A) LLVM pass C++ source<br/>lib/Transforms/*.cpp"]
  B["(B) O2T IR generator<br/>GeneratorConfig / grammar-gen / seed"]
  B -->|cv-replay| IR["generated LLVM IR (.ll)"]

  %% --- Source-intent track (recover intent from pass source) ---
  subgraph SRC["Source-intent track (design §5)"]
    TM["text / AST miner<br/>cv-mine-pass-source[-ast]"]
    F["findings<br/>(per-pass disambiguation)"]
    II["intent inference<br/>infer.py"]
    FIR["formal IR"]
    TM --> F --> II --> FIR
  end
  A --> TM

  %% --- Implementation track (verify the real compiled pass) ---
  subgraph SX["Implementation track"]
    SE["symbolic-exec of real pass C++<br/>symexec / KLEE (per-path refinement)"]
  end
  A --> SE

  %% --- Closed-loop translation validation of real opt ---
  subgraph TV["Closed-loop TV track (design §6-7)"]
    OPT["real opt -passes=X"]
    BA["before / after IR<br/>(literal emitted instructions)"]
    T["IR → SMT refinement<br/>scalar · dse · slp · mem2reg · cfg"]
    OPT --> BA --> T
  end
  IR --> OPT

  %% --- Loop / recurrence track ---
  subgraph LOOP["Loop track (design §2-4)"]
    SCEV["SCEV / Clang frontend"]
    REC["recurrence AST"]
    IND["induction · relational simulation<br/>closed-form (integer-ring)"]
    SCEV --> REC --> IND
  end
  IR --> SCEV

  %% --- Parser-agnostic prover core ---
  FIR --> CORE
  SE --> CORE
  T --> CORE
  IND --> CORE
  subgraph CORE["Prover core (parser-agnostic)"]
    Z3["Z3 — QF_BV / QF_ABV / QF_UF / QF_FP<br/>integer-ring · k-induction · theory of arrays<br/><b>premise-SAT gate (anti-vacuity)</b>"]
  end

  Z3 --> P["PROVED (unsat)"]
  Z3 --> R["REFUTED (sat)"]
  Z3 --> U["UNSUPPORTED (sound decline)"]
  R --> W["CEGAR / forward-exec<br/>→ minimized witness (.cfg / .ll)"]

  %% --- Meta-verification: what a 'proved' verdict means ---
  P --> META
  subgraph META["Meta-verification (trust base)"]
    PA["proof_audit — premises SAT + every mutation refuted"]
    CC["cross_check — 2nd solver (bitwuzla) + witness re-validation"]
    PM["parametric — re-proved at widths {8,16,32,64}, arities {2,4,8,16}"]
  end

  ORCH["Orchestrator: classify → plan → dispatch"]
  ORCH -. drives .-> TV
  ORCH -. drives .-> SRC
  ORCH -. drives .-> SX
  ORCH -. drives .-> LOOP
```

## The three verdicts

- **PROVED** (`unsat`) — the obligation holds for all inputs. Only trusted after the premise-SAT
  gate (premises are jointly satisfiable, so the proof is not vacuous), and then re-certified by the
  meta-verification layer.
- **REFUTED** (`sat`) — a counterexample exists; forward-execution / CEGAR minimizes it to a concrete
  witness (`.cfg` / `.ll`) that reproduces the miscompile.
- **UNSUPPORTED** — the shape is outside the modeled fragment; declined explicitly rather than
  falsely proved (the sound boundary — "no silent caps").

## Where each track is gated

See [claim-fixture-map.md](claim-fixture-map.md) for the fixture that gates every box:
source-intent → `extract_*_model` / `intent_inference_*`; implementation → `symexec_real_pass` /
`klee_symexec`; closed-loop TV → `scalar_ir` / `dse_ir` / `slp_ir` / `mem2reg_ir` / `cfg_shape`;
loop → `loop_induction` / `loop_simulation` / `closed_form`; meta → `proof_audit` / `cross_check` /
`parametric` / `formal_ir_vacuous_premises`.
