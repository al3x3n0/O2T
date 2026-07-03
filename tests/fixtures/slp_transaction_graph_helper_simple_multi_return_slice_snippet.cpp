namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct CmpInst {
  enum Predicate { ICMP_EQ, ICMP_NE };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
  Value *CreateICmp(CmpInst::Predicate, Value *, Value *);
  Value *CreateSelect(Value *, Value *, Value *);
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

Value *simpleMultiReturnMask(Value *ChooseAlt, Value *L, Value *R,
                             IRBuilder &Builder) {
  if (ChooseAlt)
    return Builder.CreateICmp(CmpInst::ICMP_NE, L, R);
  return Builder.CreateICmp(CmpInst::ICMP_EQ, L, R);
}

Value *loadSimpleMultiReturnMaskMemory(Value **Base, Value **Cmp,
                                       Value **Passthru,
                                       IRBuilder &Builder) {
  Value *C0 = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[0], Passthru[0]);
  Value *M0 = simpleMultiReturnMask(C0, Cmp[0], Passthru[0], Builder);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *C1 = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[1], Passthru[1]);
  Value *M1 = simpleMultiReturnMask(C1, Cmp[1], Passthru[1], Builder);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *C2 = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[2], Passthru[2]);
  Value *M2 = simpleMultiReturnMask(C2, Cmp[2], Passthru[2], Builder);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *C3 = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[3], Passthru[3]);
  Value *M3 = simpleMultiReturnMask(C3, Cmp[3], Passthru[3], Builder);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeSimpleMultiReturnHelperSliceTree(TreeEntry &Entry, Value **In,
                                               Value **Cmp, Value **Passthru,
                                               IRBuilder &Builder,
                                               TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = loadSimpleMultiReturnMaskMemory(In, Cmp, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
