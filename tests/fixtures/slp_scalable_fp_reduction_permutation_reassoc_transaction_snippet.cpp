namespace llvm {
struct Value {};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct FastMathFlags {
  void setAllowReassoc();
};
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

void vectorizeScalableFPReductionPermutationReassoc(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  unsigned LaneMap[4] = {2, 0, 3, 1};
  ElementCount VF = ElementCount::getScalable(4);
  (void)LaneMap;
  (void)VF;
  FastMathFlags FMF;
  FMF.setAllowReassoc();
  Builder.setFastMathFlags(FMF);
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Reduced = Builder.CreateFAddReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}
