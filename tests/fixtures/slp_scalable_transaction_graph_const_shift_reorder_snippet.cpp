namespace llvm {
struct Value {};
struct Type {
  static Type *getInt32Ty();
};
struct ConstantInt {
  static Value *get(Type *, unsigned);
};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And, Shl, LShr, AShr };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
  Value *CreateLShr(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

static int ReorderMask[4] = {2, 0, 3, 1};
static int ResultLaneMap[4] = {2, 0, 3, 1};

void vectorizeScalableConstShiftTree(TreeEntry &Entry, IRBuilder &Builder,
                                     TargetTransformInfo &TTI) {
  ElementCount VF = ElementCount::getScalable(4);
  (void)VF;
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, 0);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult =
        Builder.CreateLShr(VectorAdd, ConstantInt::get(Type::getInt32Ty(), 1));
    replaceScalarUses(Entry, VectorResult);
    (void)ReorderMask;
    (void)ResultLaneMap;
  }
}
