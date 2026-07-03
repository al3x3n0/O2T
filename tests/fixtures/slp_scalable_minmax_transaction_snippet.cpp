namespace llvm {
struct Value {};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct Instruction {
  enum CmpPredicate { ICMP_SLT, ICMP_SGT, ICMP_ULT, ICMP_UGT };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
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

void vectorizeScalableMin(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Cmp = Builder.CreateICmp(Instruction::ICMP_SLT, LHS, RHS);
    Value *Selected = Builder.CreateSelect(Cmp, LHS, RHS);
    Value *VectorResult = Builder.CreateSMin(LHS, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
