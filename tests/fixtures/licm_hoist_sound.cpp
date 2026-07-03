// A sound LICM-style pass: every hoist fold checks loop-invariance AND a safety condition
// (speculatable, or guaranteed to execute), so the deep loop-structural model proves it.
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

bool hoistSpeculatable(Instruction *I, Loop *L) {
  if (isLoopInvariant(L, I) && isSafeToSpeculativelyExecute(I)) {
    hoistToPreheader(I, L);
    return true;
  }
  return false;
}

bool hoistGuaranteed(Instruction *I, Loop *L) {
  if (isLoopInvariant(L, I) && isGuaranteedToExecute(I, L)) {
    hoistToPreheader(I, L);
    return true;
  }
  return false;
}
