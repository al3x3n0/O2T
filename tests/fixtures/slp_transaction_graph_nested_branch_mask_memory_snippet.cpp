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

Value *loadNestedBranchMaskMemory(Value **Base, Value **A, Value **Gate,
                                  Value **Passthru, IRBuilder &Builder) {
  Value *C0 = Builder.CreateICmp(CmpInst::ICMP_EQ, Gate[0], Passthru[0]);
  Value *D0 = Builder.CreateICmp(CmpInst::ICMP_NE, Gate[0], Base[0]);
  Value *T0 = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Base[0]);
  Value *F0 = Builder.CreateICmp(CmpInst::ICMP_EQ, Base[0], Passthru[0]);
  Value *N0 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *M0;
  if (C0) {
    if (D0) {
      M0 = T0;
    } else {
      M0 = F0;
    }
  } else {
    M0 = N0;
  }
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeNestedBranchMaskMemoryTree(TreeEntry &Entry, Value **In,
                                         Value **A, Value **Gate,
                                         Value **Passthru, IRBuilder &Builder,
                                         TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      isProfitable(Entry, TTI)) {
    Value *LHS = loadNestedBranchMaskMemory(In, A, Gate, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    replaceScalarUses(Entry, VectorResult);
  }
}
