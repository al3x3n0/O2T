namespace llvm {
struct Value {};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct FastMathFlags {};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  void setFastMathFlags(FastMathFlags);
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

void vectorizeScalableFastFPReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  FastMathFlags FMF;
  Builder.setFastMathFlags(FMF);
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Reduced = Builder.CreateFAddReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}
