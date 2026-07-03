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
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};

bool discoverCandidate(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] && Entry.Scalars[3];
}

bool checkLegality(TreeEntry &Entry) {
  return canVectorize(Entry) && isValidElementType(Entry);
}

bool isProfitable(TreeEntry &Entry, TargetTransformInfo &TTI) {
  return getEntryCost(Entry, TTI) < 4;
}

Value *emitVectorOp(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = packOperand(Entry, 0);
  Value *RHS = packOperand(Entry, 1);
  Value *Cmp = Builder.CreateICmp(Instruction::ICMP_UGT, LHS, RHS);
  Value *Selected = Builder.CreateSelect(Cmp, LHS, RHS);
  return Builder.CreateUMax(LHS, RHS);
}

void replaceExternalUses(TreeEntry &Entry, Value *VectorResult) {
  replaceScalarUses(Entry, VectorResult);
}
