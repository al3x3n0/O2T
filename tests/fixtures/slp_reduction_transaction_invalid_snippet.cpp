namespace llvm {
struct Value {};
struct TargetTransformInfo {};
struct IRBuilder {
  Value *CreateAddReduce(Value *);
};
enum Instruction { Add, Mul };
} // namespace llvm

using namespace llvm;

struct TreeEntry {
  Value *Scalars[32];
};

bool canVectorize(TreeEntry &);
bool allSameOpcode(TreeEntry &, Instruction);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
void replaceScalarUses(TreeEntry &, Value *);

Value *buildLHS(TreeEntry &Entry) {
  return Entry.Scalars[0];
}

void vectorizeReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && allSameOpcode(Entry, Instruction::Mul) &&
      isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = buildLHS(Entry);
    Value *Reduced = Builder.CreateAddReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}
