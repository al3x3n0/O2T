namespace llvm {
struct Value {};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateFAddReduce(Value *);
};
struct TargetTransformInfo {};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

void vectorizeFloatingReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Reduced = Builder.CreateFAddReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}
