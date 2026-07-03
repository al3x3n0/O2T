// SimplifyCFG-style if-conversion folds, mined to the deep diamond->select contract and
// discharged: folding `phi [then-val, ThenBB], [else-val, ElseBB]` over `br %cond, ThenBB, ElseBB`
// into a select is sound iff the select reads then-val when the condition holds and else-val
// otherwise. A fold that swaps the operands (or flips the condition without swapping) is refuted.
namespace llvm {
struct Value {};
struct BasicBlock {};
struct BranchInst { Value *getCondition(); };
struct PHINode { Value *getIncomingValueForBlock(BasicBlock *); };
struct IRBuilder {
  Value *CreateSelect(Value *, Value *, Value *);
  Value *CreateNot(Value *);
};
} // namespace llvm

using namespace llvm;

// SOUND: select cond, then-value, else-value -- the identity if-conversion.
Value *foldDiamondToSelect(IRBuilder &B, BranchInst *BI, PHINode *PN,
                           BasicBlock *ThenBB, BasicBlock *ElseBB) {
  Value *Cond = BI->getCondition();
  Value *TrueV = PN->getIncomingValueForBlock(ThenBB);
  Value *FalseV = PN->getIncomingValueForBlock(ElseBB);
  return B.CreateSelect(Cond, TrueV, FalseV);
}

// SOUND: negate the condition AND swap the operands -- equivalent to the identity.
Value *foldDiamondNegatedSwapped(IRBuilder &B, BranchInst *BI, PHINode *PN,
                                 BasicBlock *ThenBB, BasicBlock *ElseBB) {
  Value *Cond = BI->getCondition();
  Value *TrueV = PN->getIncomingValueForBlock(ThenBB);
  Value *FalseV = PN->getIncomingValueForBlock(ElseBB);
  Value *NotCond = B.CreateNot(Cond);
  return B.CreateSelect(NotCond, FalseV, TrueV);
}

// UNSOUND (planted): operands swapped WITHOUT negating the condition -- the select returns the
// else-value when the condition holds. Must be REFUTED.
Value *foldDiamondSwappedOperands(IRBuilder &B, BranchInst *BI, PHINode *PN,
                                  BasicBlock *ThenBB, BasicBlock *ElseBB) {
  Value *Cond = BI->getCondition();
  Value *TrueV = PN->getIncomingValueForBlock(ThenBB);
  Value *FalseV = PN->getIncomingValueForBlock(ElseBB);
  return B.CreateSelect(Cond, FalseV, TrueV);
}
