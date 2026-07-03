// LICM-style hoisting folds, mined to the deep loop-structural contract and discharged: hoisting
// a computation out of a loop is sound only if its operands are loop-invariant AND it is safe to
// run unconditionally in the preheader (guaranteed to execute, or speculatable / non-trapping).
// A fold that hoists a trapping op guarded only by loop-invariance is unsound -- refuted.
namespace llvm {
struct Value {};
struct Instruction : Value {};
struct Loop {};

bool isLoopInvariant(Loop *, Instruction *);
bool isSafeToSpeculativelyExecute(Instruction *);
bool isGuaranteedToExecute(Instruction *, Loop *);
void hoistToPreheader(Instruction *, Loop *);
} // namespace llvm

using namespace llvm;

// SOUND: hoist only invariant ops that are safe to speculate (the real LICM guard).
bool hoistInvariantSpeculatable(Instruction *I, Loop *L) {
  if (isLoopInvariant(L, I) && isSafeToSpeculativelyExecute(I)) {
    hoistToPreheader(I, L);
    return true;
  }
  return false;
}

// SOUND: hoist an invariant op that is guaranteed to execute (so any trap already happened).
bool hoistInvariantGuaranteed(Instruction *I, Loop *L) {
  if (isLoopInvariant(L, I) && isGuaranteedToExecute(I, L)) {
    hoistToPreheader(I, L);
    return true;
  }
  return false;
}

// UNSOUND (planted): hoists any loop-invariant op with NO safety check. A trapping op (e.g. a
// division) that the loop might skip is now run unconditionally in the preheader -> a new trap.
bool hoistInvariantOnly(Instruction *I, Loop *L) {
  if (isLoopInvariant(L, I)) {
    hoistToPreheader(I, L);
    return true;
  }
  return false;
}
