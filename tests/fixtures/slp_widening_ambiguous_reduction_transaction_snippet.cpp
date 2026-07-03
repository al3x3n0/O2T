namespace llvm {
struct Value {};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateZExt(Value *);
  Value *CreateAddReduce(Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeAmbiguousWideningReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Wide = Builder.CreateZExt(LHS);
    Value *Reduced = Builder.CreateAddReduce(Wide);
    replaceScalarUses(Entry, Reduced);
  }
}
