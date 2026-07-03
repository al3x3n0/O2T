namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[8];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceAllUsesWith(Value *, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[8] = {4, 0, 6, 2, 7, 3, 5, 1};
static int ResultLaneMap[8] = {4, 0, 6, 2, 7, 3, 5, 1};

bool discoverCandidate(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] && Entry.Scalars[3] &&
         Entry.Scalars[4] && Entry.Scalars[5] && Entry.Scalars[6] && Entry.Scalars[7];
}

bool checkLegality(TreeEntry &Entry) {
  return allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry);
}

bool isProfitable(TreeEntry &Entry, TargetTransformInfo &TTI) {
  return getEntryCost(Entry, TTI) < 8;
}

Value *emitVectorOp(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = packOperand(Entry, 0);
  Value *RHS = packOperand(Entry, 1);
  return Builder.CreateAdd(LHS, RHS);
}

void replaceExternalUses(TreeEntry &Entry, Value *VectorResult) {
  for (int I = 0; I < 8; ++I)
    replaceAllUsesWith(Entry.Scalars[ResultLaneMap[I]], VectorResult);
}
