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
  Value *CreateAdd(Value *, Value *);
  Value *CreateXor(Value *, Value *);
};
struct TargetTransformInfo {};

bool allSameOpcode(TreeEntry &, Instruction::BinaryOps);
bool isValidElementType(TreeEntry &);
bool isProfitable(TreeEntry &, TargetTransformInfo &);
Value *packOperand(TreeEntry &, unsigned);
Value *buildPack(Value *, Value *, Value *, Value *);
void replaceScalarUses(TreeEntry &, Value *);
} // namespace llvm

using namespace llvm;

Value *deepMask4(Value *L, Value *R, IRBuilder &Builder) {
  return Builder.CreateICmp(CmpInst::ICMP_EQ, L, R);
}
Value *deepMask3(Value *L, Value *R, IRBuilder &Builder) {
  return deepMask4(L, R, Builder);
}
Value *deepMask2(Value *L, Value *R, IRBuilder &Builder) {
  return deepMask3(L, R, Builder);
}
Value *deepMask1(Value *L, Value *R, IRBuilder &Builder) {
  return deepMask2(L, R, Builder);
}
Value *deepMask0(Value *L, Value *R, IRBuilder &Builder) {
  return deepMask1(L, R, Builder);
}

Value *loadDeepChainMaskMemory(Value **Base, Value **Cmp, Value **Passthru,
                               IRBuilder &Builder) {
  Value *M0 = deepMask0(Cmp[0], Passthru[0], Builder);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1 = deepMask0(Cmp[1], Passthru[1], Builder);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2 = deepMask0(Cmp[2], Passthru[2], Builder);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3 = deepMask0(Cmp[3], Passthru[3], Builder);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeDeepChainHelperSliceTree(TreeEntry &Entry, Value **In,
                                       Value **Cmp, Value **Passthru,
                                       IRBuilder &Builder,
                                       TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = loadDeepChainMaskMemory(In, Cmp, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
