namespace llvm {
struct Value {};
struct Instruction {
  enum CmpPredicate { ICMP_SLT, ICMP_SGT, ICMP_ULT, ICMP_UGT };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
  Value *CreateICmp(Instruction::CmpPredicate, Value *, Value *);
  Value *CreateSelect(Value *, Value *, Value *);
  Value *CreateSMin(Value *, Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeMinGraph(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Extra = packOperand(Entry, 2);
    Value *AddTmp = Builder.CreateAdd(LHS, RHS);
    Value *Cmp = Builder.CreateICmp(Instruction::ICMP_SLT, AddTmp, Extra);
    Value *Selected = Builder.CreateSelect(Cmp, AddTmp, Extra);
    Value *VectorResult = Builder.CreateSMin(AddTmp, Extra);
    (void)Selected;
    replaceScalarUses(Entry, VectorResult);
  }
}
