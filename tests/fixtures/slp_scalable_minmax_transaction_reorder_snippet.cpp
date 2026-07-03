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
  Value *CreateUMax(Value *, Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceAllUsesWith(Value *, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};
static int ResultLaneMap[4] = {2, 0, 3, 1};

bool discoverCandidate(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] && Entry.Scalars[3];
}

bool checkLegality(TreeEntry &Entry) {
  return canVectorize(Entry) && isValidElementType(Entry);
}

bool isProfitable(TreeEntry &Entry, TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  return getEntryCost(Entry, TTI) < 4;
}

Value *emitVectorOp(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = packOperand(Entry, 0);
  Value *RHS = packOperand(Entry, 1);
  Value *Cmp = Builder.CreateICmp(Instruction::ICMP_UGT, LHS, RHS);
  Value *Selected = Builder.CreateSelect(Cmp, LHS, RHS);
  (void)Selected;
  return Builder.CreateUMax(LHS, RHS);
}

void replaceExternalUses(TreeEntry &Entry, Value *VectorResult) {
  for (int I = 0; I < 4; ++I)
    replaceAllUsesWith(Entry.Scalars[ResultLaneMap[I]], VectorResult);
}
