namespace llvm {
struct LLVMContext {};
struct Type {
  static Type *getInt16Ty(LLVMContext &);
  static Type *getInt32Ty(LLVMContext &);
};
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

void vectorizeWideningReduction(TreeEntry &Entry, IRBuilder &Builder, TargetTransformInfo &TTI, LLVMContext &Ctx) {
  if (canVectorize(Entry) && isValidElementType(Entry) && isProfitable(Entry, TTI)) {
    Type *InputTy = Type::getInt16Ty(Ctx);
    Type *WideTy = Type::getInt32Ty(Ctx);
    Value *LHS = packOperand(Entry, 0);
    Value *Wide = Builder.CreateZExt(LHS);
    Value *Reduced = Builder.CreateAddReduce(Wide);
    replaceScalarUses(Entry, Reduced);
  }
}
