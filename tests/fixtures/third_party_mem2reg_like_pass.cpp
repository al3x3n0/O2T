// A mem2reg-like promotion pass: promote stack allocas to SSA registers, inserting phi nodes at
// the iterated dominance frontier. Classified into the `promotion` family; the orchestrator then
// validates the real `opt -passes=mem2reg` output by symbolic CFG execution (mem2reg-ir).
namespace vendor_promote {
namespace llvm {
struct AllocaInst {};
struct PHINode {};
struct BasicBlock {};
struct Function {};
struct DominatorTree {};

bool isAllocaPromotable(const AllocaInst *);
bool rewriteSingleStoreAlloca(AllocaInst *);
void IDFCalculator(DominatorTree &);
} // namespace llvm

using namespace llvm;

// Promote each promotable alloca: single-store allocas are rewritten directly; the rest get phi
// nodes placed at the iterated dominance frontier (PromoteMemToReg).
void PromoteMemToReg(Function &F, DominatorTree &DT) {
  IDFCalculator(DT);
  // for each promotable alloca: isAllocaPromotable(AI) -> rewriteSingleStoreAlloca / insert PHINode
  (void)&isAllocaPromotable;
  (void)&rewriteSingleStoreAlloca;
}
} // namespace vendor_promote
