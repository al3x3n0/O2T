namespace llvm {
struct Value {};
struct Instruction {
  enum CmpPredicate { ICMP_SLT, ICMP_SGT, ICMP_ULT, ICMP_UGT };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateICmp(Instruction::CmpPredicate, Value *, Value *);
  Value *CreateSelect(Value *, Value *, Value *);
  Value *CreateUMax(Value *, Value *);
  Value *CreateXor(Value *, Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeMixedGraph(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Mask = packOperand(Entry, 2);
    Value *Cmp = Builder.CreateICmp(Instruction::ICMP_UGT, LHS, RHS);
    Value *Selected = Builder.CreateSelect(Cmp, LHS, RHS);
    Value *MaxTmp = Builder.CreateUMax(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(MaxTmp, Mask);
    (void)Selected;
    replaceScalarUses(Entry, VectorResult);
  }
}
