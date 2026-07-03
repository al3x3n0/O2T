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

Value *loadBranchMaskMemory(Value **Base, Value **A, Value **Passthru,
                            IRBuilder &Builder) {
  Value *C0 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *T0 = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Base[0]);
  Value *F0 = Builder.CreateICmp(CmpInst::ICMP_EQ, Base[0], Passthru[0]);
  Value *M0;
  if (C0) {
    M0 = T0;
  } else {
    M0 = F0;
  }
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *C1 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
  Value *T1 = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Base[1]);
  Value *F1 = Builder.CreateICmp(CmpInst::ICMP_EQ, Base[1], Passthru[1]);
  Value *M1;
  if (C1) M1 = T1; else M1 = F1;
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *C2 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
  Value *T2 = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Base[2]);
  Value *F2 = Builder.CreateICmp(CmpInst::ICMP_EQ, Base[2], Passthru[2]);
  Value *M2;
  if (C2) M2 = T2; else M2 = F2;
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *C3 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
  Value *T3 = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Base[3]);
  Value *F3 = Builder.CreateICmp(CmpInst::ICMP_EQ, Base[3], Passthru[3]);
  Value *M3;
  if (C3) M3 = T3; else M3 = F3;
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeBranchMaskMemoryTree(TreeEntry &Entry, Value **In, Value **Out,
                                   Value **A, Value **Passthru,
                                   IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadBranchMaskMemory(In, A, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Builder.CreateMaskedStore(VectorResult, Out[0], Passthru[0]);
    Builder.CreateMaskedStore(VectorResult, Out[1], Passthru[1]);
    Builder.CreateMaskedStore(VectorResult, Out[2], Passthru[2]);
    Builder.CreateMaskedStore(VectorResult, Out[3], Passthru[3]);
    replaceScalarUses(Entry, VectorResult);
  }
}
