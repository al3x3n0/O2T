namespace llvm {
struct LLVMContext {};
struct Value {};
struct Type {};
struct IntegerType : Type {
  static Type *get(LLVMContext &, unsigned);
};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  LLVMContext &getContext();
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
    unsigned InputBits = 16;
    unsigned AccumulatorBits = 32;
    LLVMContext &Context = Builder.getContext();
    Type *InputTy = IntegerType::get(Context, InputBits);
    (void)InputTy;
    Type *WideTy = IntegerType::get(Context, AccumulatorBits);
    Value *LHS = packOperand(Entry, 0);
    Value *Wide = Builder.CreateZExt(LHS, WideTy);
    Value *Reduced = Builder.CreateAddReduce(Wide);
    replaceScalarUses(Entry, Reduced);
  }
}
