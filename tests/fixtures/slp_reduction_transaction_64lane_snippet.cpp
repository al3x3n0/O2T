namespace llvm {
struct Value {};
struct TreeEntry {
  Value *Scalars[64];
};
struct IRBuilder {
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

void vectorizeReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Reduced = Builder.CreateAddReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}
