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
  Value *CreateMul(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *buildPack(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

Value *buildAdd(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = buildPack(Entry, 0);
  Value *RHS = buildPack(Entry, 1);
  return Builder.CreateAdd(LHS, RHS);
}

Value *buildMul(Value *VectorTmp, TreeEntry &Entry, IRBuilder &Builder) {
  Value *Extra = buildPack(Entry, 2);
  return Builder.CreateMul(VectorTmp, Extra);
}

bool discoverCandidate(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] && Entry.Scalars[3];
}

void vectorizeTree(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (discoverCandidate(Entry) && allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *VectorTmp = buildAdd(Entry, Builder);
    Value *VectorResult = buildMul(VectorTmp, Entry, Builder);
    replaceScalarUses(Entry, VectorResult);
  }
}
