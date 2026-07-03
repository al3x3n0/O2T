namespace llvm {
struct Value {};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAddReduce(Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
int getEntryCost(TreeEntry &, TargetTransformInfo &);
Value *buildPack(TreeEntry &, unsigned, int *);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {0, 1, 2, 3};

bool buildTree_rec(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] &&
         Entry.Scalars[3];
}

bool isReductionLegal(TreeEntry &Entry) {
  return canVectorize(Entry) && isValidElementType(Entry);
}

bool isReductionProfitable(TreeEntry &Entry, TargetTransformInfo &TTI) {
  return getEntryCost(Entry, TTI) < 8;
}

Value *collectReductionOperand(TreeEntry &Entry) {
  (void)Entry.Scalars[ReorderMask[0]];
  return buildPack(Entry, 0, ReorderMask);
}

Value *materializeReduction(TreeEntry &Entry, IRBuilder &Builder) {
  Value *LHS = collectReductionOperand(Entry);
  return Builder.CreateAddReduce(LHS);
}

void commitReductionResult(TreeEntry &Entry, Value *Reduced) {
  replaceScalarUses(Entry, Reduced);
}

void vectorizeReduction(TreeEntry &Entry, IRBuilder &Builder,
                        TargetTransformInfo &TTI) {
  if (buildTree_rec(Entry) && isReductionLegal(Entry) &&
      isReductionProfitable(Entry, TTI)) {
    Value *Reduced = materializeReduction(Entry, Builder);
    commitReductionResult(Entry, Reduced);
  }
}
