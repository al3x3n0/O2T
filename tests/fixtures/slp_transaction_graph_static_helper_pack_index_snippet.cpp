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

constexpr unsigned LHSIndex = 0;
enum { RHSIndex = LHSIndex + 1, ExtraIndex = RHSIndex + 1 };

Value *buildLHS(TreeEntry &Entry) { return buildPack(Entry, LHSIndex); }
Value *buildRHS(TreeEntry &Entry) { return buildPack(Entry, RHSIndex); }
Value *buildExtra(TreeEntry &Entry) { return buildPack(Entry, ExtraIndex); }

bool discoverCandidate(TreeEntry &Entry) {
  return Entry.Scalars[0] && Entry.Scalars[1] && Entry.Scalars[2] && Entry.Scalars[3];
}

void vectorizeStaticHelperPackIndexTree(TreeEntry &Entry, IRBuilder &Builder,
                                        TargetTransformInfo &TTI) {
  if (discoverCandidate(Entry) && allSameOpcode(Entry, Instruction::Add) &&
      isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = buildLHS(Entry);
    Value *RHS = buildRHS(Entry);
    Value *Extra = buildExtra(Entry);
    Value *VectorTmp = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateMul(VectorTmp, Extra);
    replaceScalarUses(Entry, VectorResult);
  }
}
