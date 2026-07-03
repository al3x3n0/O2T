// A sound SimplifyCFG-style if-conversion pass: every fold binds the select operands to match
// the diamond (identity, or negate-and-swap), so the deep diamond->select model proves it.
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

Value *foldDiamond(IRBuilder &B, BranchInst *BI, PHINode *PN,
                   BasicBlock *ThenBB, BasicBlock *ElseBB) {
  Value *Cond = BI->getCondition();
  Value *TrueV = PN->getIncomingValueForBlock(ThenBB);
  Value *FalseV = PN->getIncomingValueForBlock(ElseBB);
  return B.CreateSelect(Cond, TrueV, FalseV);
}

Value *foldDiamondNegated(IRBuilder &B, BranchInst *BI, PHINode *PN,
                          BasicBlock *ThenBB, BasicBlock *ElseBB) {
  Value *Cond = BI->getCondition();
  Value *TrueV = PN->getIncomingValueForBlock(ThenBB);
  Value *FalseV = PN->getIncomingValueForBlock(ElseBB);
  Value *NotCond = B.CreateNot(Cond);
  return B.CreateSelect(NotCond, FalseV, TrueV);
}
