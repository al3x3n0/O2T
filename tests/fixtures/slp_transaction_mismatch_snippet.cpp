namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateMul(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceAllUsesWith(Value *, Value *);
} // namespace llvm

using namespace llvm;

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
  Value *LHS = packOperand(Entry, 0);
  Value *RHS = packOperand(Entry, 1);
  return Builder.CreateMul(LHS, RHS);
}

void replaceExternalUses(TreeEntry &Entry, Value *VectorResult) {
  replaceAllUsesWith(Entry.Scalars[0], VectorResult);
}
