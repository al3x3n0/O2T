namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct CmpInst {
  enum Predicate { ICMP_EQ };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateICmp(CmpInst::Predicate, Value *, Value *);
  Value *CreateMaskedLoad(Value *, Value *, Value *);
  void CreateMaskedStore(Value *, Value *, Value *);
  Value *CreateAdd(Value *, Value *);
  Value *CreateXor(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
bool noAlias(Value **, Value **);
Value *packOperand(TreeEntry &, unsigned);
Value *buildPack(Value *, Value *, Value *, Value *);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

Value *makeNestedEqMask(Value *L, Value *R, IRBuilder &Builder) {
  return Builder.CreateICmp(CmpInst::ICMP_EQ, L, R);
}

Value *loadNestedInner(Value **Ptr, Value **Cmp, Value **Pass,
                       IRBuilder &Builder) {
  Value *M0 = makeNestedEqMask(Cmp[0], Pass[0], Builder);
  Value *L0 = Builder.CreateMaskedLoad(Ptr[0], M0, Pass[0]);
  Value *M1 = makeNestedEqMask(Cmp[1], Pass[1], Builder);
  Value *L1 = Builder.CreateMaskedLoad(Ptr[1], M1, Pass[1]);
  Value *M2 = makeNestedEqMask(Cmp[2], Pass[2], Builder);
  Value *L2 = Builder.CreateMaskedLoad(Ptr[2], M2, Pass[2]);
  Value *M3 = makeNestedEqMask(Cmp[3], Pass[3], Builder);
  Value *L3 = Builder.CreateMaskedLoad(Ptr[3], M3, Pass[3]);
  return buildPack(L0, L1, L2, L3);
}

Value *loadNestedOuter(Value **Base, Value **Cmp, Value **Passthru,
                       IRBuilder &Builder) {
  return loadNestedInner(Base, Cmp, Passthru, Builder);
}

void vectorizeNestedHelperMemoryTree(TreeEntry &Entry, Value **In, Value **Out,
                                     Value **Cmp, Value **Passthru,
                                     IRBuilder &Builder,
                                     TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadNestedOuter(In, Cmp, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0 = makeNestedEqMask(Cmp[0], Passthru[0], Builder);
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1 = makeNestedEqMask(Cmp[1], Passthru[1], Builder);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2 = makeNestedEqMask(Cmp[2], Passthru[2], Builder);
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3 = makeNestedEqMask(Cmp[3], Passthru[3], Builder);
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
