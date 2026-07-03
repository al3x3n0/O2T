namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateAdd(Value *, Value *);
  Value *CreateMul(Value *, Value *);
  Value *CreateXor(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

constexpr unsigned LHSIndex = 0;
enum { RHSIndex = LHSIndex + 1, ScaleIndex = RHSIndex + 1, MaskIndex = ScaleIndex + 1 };

void vectorizeStaticPackIndexTree(TreeEntry &Entry, IRBuilder &Builder,
                                  TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = packOperand(Entry, LHSIndex);
    Value *RHS = packOperand(Entry, RHSIndex);
    Value *Scale = packOperand(Entry, ScaleIndex);
    Value *Mask = packOperand(Entry, MaskIndex);
    Value *AddTmp = Builder.CreateAdd(LHS, RHS);
    Value *MulTmp = Builder.CreateMul(AddTmp, Scale);
    Value *VectorResult = Builder.CreateXor(MulTmp, Mask);
    replaceScalarUses(Entry, VectorResult);
  }
}
