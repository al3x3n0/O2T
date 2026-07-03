namespace llvm {
struct Value {};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
};
struct TargetTransformInfo {};

int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *buildPack(TreeEntry &, unsigned, int *);
void replaceAllUsesWith(Value *, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};

bool buildTree_rec(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] &&
         Entry.Scalars[3];
}

bool opaqueVectorLegality(TreeEntry &Entry) {
  return Entry.Scalars[0] != Entry.Scalars[1];
}

bool isTreeProfitable(TreeEntry &Entry, TargetTransformInfo &TTI) {
  return getEntryCost(Entry, TTI) < 7;
}

Value *collectOperand(TreeEntry &Entry, unsigned Operand) {
  (void)Entry.Scalars[ReorderMask[0]];
  return buildPack(Entry, Operand, ReorderMask);
}

Value *buildLeftPack(TreeEntry &Entry) {
  return collectOperand(Entry, 0);
}

Value *buildRightPack(TreeEntry &Entry) {
  return collectOperand(Entry, 1);
}

Value *materializeVector(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = buildLeftPack(Entry);
  Value *RHS = buildRightPack(Entry);
  return Builder.CreateAdd(LHS, RHS);
}

void commitVectorizedUses(TreeEntry &Entry, Value *VectorResult) {
  replaceAllUsesWith(Entry.Scalars[0], VectorResult);
}

void vectorizeTree(TreeEntry &Entry, IRBuilder &Builder,
                   TargetTransformInfo &TTI) {
  if (buildTree_rec(Entry) && opaqueVectorLegality(Entry) &&
      isTreeProfitable(Entry, TTI)) {
    Value *VectorResult = materializeVector(Entry, Builder);
    commitVectorizedUses(Entry, VectorResult);
  }
}
