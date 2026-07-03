namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct Forest {
  TreeEntry Entries[2];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *buildPack(TreeEntry &, unsigned, int *);
void replaceAllUsesWith(Value *, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};

bool inspectNested(Forest &Tree) {
  return Tree.Entries[0].Scalars[ReorderMask[1]];
}

bool inspectPointer(TreeEntry *Entry) {
  return Entry->Scalars[0];
}

Value *buildLHS(TreeEntry &Entry) {
  Value *S0 = Entry.Scalars[0];
  Value *S1 = Entry.Scalars[ReorderMask[1]];
  (void)S0;
  (void)S1;
  return buildPack(Entry, 0, ReorderMask);
}

Value *buildRHS(TreeEntry &Entry) {
  return buildPack(Entry, 1, ReorderMask);
}

bool discoverCandidate(TreeEntry &Entry) {
  Forest Tree;
  return Tree.Entries[0].Scalars[ReorderMask[1]] && inspectNested(Tree) &&
         inspectPointer(&Entry) && Entry.Scalars[0] && Entry.Scalars[1] &&
         Entry.Scalars[2] && Entry.Scalars[3];
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
  Entry.Scalars[1] = VectorResult;
  replaceAllUsesWith(Entry.Scalars[0], VectorResult);
}
