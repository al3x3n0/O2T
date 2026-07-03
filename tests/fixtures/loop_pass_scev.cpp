//===-- Representative SCEV idioms from LLVM loop passes -------------------===//
// Curated excerpts mirroring the ScalarEvolution API calls that real loop passes
// (LoopStrengthReduce, IndVarSimplify) use to express a transform. The recognizer
// (cv-mine-pass-scev.py) lifts the (precondition -> rewrite) recurrence INTENT from
// each getMulExpr/getAddRecExpr pair and discharges it with the relational prover.
// It does NOT compile or run LLVM; symbols (C, D) are universally-quantified consts.
//===----------------------------------------------------------------------===//

#include "llvm/Analysis/ScalarEvolution.h"
using namespace llvm;

// LSR core: a loop-variant product (c * i) is replaced by an add-recurrence
// {0,+,c}. Intent: the multiply-by-IV becomes a running add. Sound iff the
// recurrence step equals the eliminated product's coefficient.
const SCEV *strengthReduce(ScalarEvolution &SE, const SCEV *IV, const SCEV *C, Loop *L) {
  const SCEV *Product = SE.getMulExpr(C, IV);                       // c * {0,+,1}
  const SCEV *Stride  = SE.getAddRecExpr(SE.getConstant(0), C, L,   // {0,+,c}
                                         SCEV::FlagAnyWrap);
  return rewriteUses(Product, Stride);
}

// Deliberately WRONG rewrite: the replacement recurrence steps by d, not the
// eliminated product's coefficient c. The prover must REFUSE this (teeth).
const SCEV *wrongStride(ScalarEvolution &SE, const SCEV *IV, const SCEV *C,
                        const SCEV *D, Loop *L) {
  const SCEV *Product = SE.getMulExpr(IV, C);                       // c * {0,+,1}
  const SCEV *Stride  = SE.getAddRecExpr(SE.getConstant(0), D, L);  // {0,+,d}  -- BUG
  return rewriteUses(Product, Stride);
}

// A loop-invariant product (no IV operand): there is no strength-reduction intent
// to lift, and no add-recurrence is built. The recognizer must DECLARE this
// skipped, not silently report success.
const SCEV *invariantOnly(ScalarEvolution &SE, const SCEV *A, const SCEV *B) {
  return SE.getMulExpr(A, B);
}
