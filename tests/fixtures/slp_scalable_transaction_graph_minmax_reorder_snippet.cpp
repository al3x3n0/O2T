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
  Value *CreateAdd(Value *, Value *);
  Value *CreateICmp(Instruction::CmpPredicate, Value *, Value *);
  Value *CreateSelect(Value *, Value *, Value *);
  Value *CreateUMax(Value *, Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};
static int ResultLaneMap[4] = {2, 0, 3, 1};

void vectorizeScalableMinGraph(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *Extra = packOperand(Entry, 2);
    Value *AddTmp = Builder.CreateAdd(LHS, RHS);
    Value *Cmp = Builder.CreateICmp(Instruction::ICMP_UGT, AddTmp, Extra);
    Value *Selected = Builder.CreateSelect(Cmp, AddTmp, Extra);
    Value *VectorResult = Builder.CreateUMax(AddTmp, Extra);
    (void)Selected;
    replaceScalarUses(Entry, VectorResult);
    (void)ReorderMask;
    (void)ResultLaneMap;
  }
}
