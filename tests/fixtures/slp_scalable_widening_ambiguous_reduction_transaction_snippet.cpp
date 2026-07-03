namespace llvm {
struct Value {};
struct Type {
  static Type *getInt32Ty();
};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateZExt(Value *, Type *);
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

void vectorizeScalableReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Wide = Builder.CreateZExt(LHS, Type::getInt32Ty());
    Value *Reduced = Builder.CreateAddReduce(Wide);
    replaceScalarUses(Entry, Reduced);
  }
}
