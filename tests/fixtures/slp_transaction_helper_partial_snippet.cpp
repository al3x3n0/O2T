namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *buildPack(TreeEntry &, unsigned);
void replaceAllUsesWith(Value *, Value *);
} // namespace llvm

using namespace llvm;

Value *buildLHS(TreeEntry &Entry) {
  (void)Entry.Scalars[0];
  (void)Entry.Scalars[1];
  return buildPack(Entry, 0);
}

Value *buildRHS(TreeEntry &Entry) {
  (void)Entry.Scalars[0];
  (void)Entry.Scalars[1];
  return buildPack(Entry, 1);
}

bool discoverCandidate(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] && Entry.Scalars[3];
}

bool checkLegality(TreeEntry &Entry) {
  return allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry);
}

bool isProfitable(TreeEntry &Entry, TargetTransformInfo &TTI) {
  return getEntryCost(Entry, TTI) < 4;
}

Value *emitVectorOp(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = buildLHS(Entry);
  Value *RHS = buildRHS(Entry);
  return Builder.CreateAdd(LHS, RHS);
}

void replaceExternalUses(TreeEntry &Entry, Value *VectorResult) {
  replaceAllUsesWith(Entry.Scalars[0], VectorResult);
}
