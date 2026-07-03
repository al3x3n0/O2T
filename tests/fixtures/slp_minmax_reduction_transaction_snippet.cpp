namespace llvm {
struct Value {};
struct TargetTransformInfo {};
struct IRBuilder {
  Value *CreateSMinReduce(Value *);
  Value *CreateUMaxReduce(Value *);
};
} // namespace llvm

using namespace llvm;

struct TreeEntry {
  Value *Scalars[4];
};

bool canVectorize(TreeEntry &);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, int);
void replaceScalarUses(TreeEntry &, Value *);

void vectorizeSMinReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Reduced = Builder.CreateSMinReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}

void vectorizeUMaxReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *Reduced = Builder.CreateUMaxReduce(LHS);
    replaceScalarUses(Entry, Reduced);
  }
}
