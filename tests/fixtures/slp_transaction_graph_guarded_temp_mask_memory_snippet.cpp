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
  Value *CreateAnd(Value *, Value *);
  Value *CreateOr(Value *, Value *);
  Value *CreateLoad(Value *);
  void CreateStore(Value *, Value *);
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

Value *loadGuardedTempMaskMemory(Value **Base, Value **A, Value **Passthru,
                                 IRBuilder &Builder) {
  Value *M0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *M0B = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Base[0]);
  Value *M0 = Builder.CreateAnd(M0A, M0B);
  Value *L0 = Passthru[0];
  if (M0) {
    L0 = Builder.CreateLoad(Base[0]);
  }
  Value *M1A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
  Value *M1B = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Base[1]);
  Value *M1 = Builder.CreateAnd(M1A, M1B);
  Value *L1 = Passthru[1];
  if (M1) {
    L1 = Builder.CreateLoad(Base[1]);
  }
  Value *M2A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
  Value *M2B = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Base[2]);
  Value *M2 = Builder.CreateAnd(M2A, M2B);
  Value *L2 = Passthru[2];
  if (M2) {
    L2 = Builder.CreateLoad(Base[2]);
  }
  Value *M3A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
  Value *M3B = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Base[3]);
  Value *M3 = Builder.CreateAnd(M3A, M3B);
  Value *L3 = Passthru[3];
  if (M3) {
    L3 = Builder.CreateLoad(Base[3]);
  }
  return buildPack(L0, L1, L2, L3);
}

void vectorizeGuardedTempMaskMemoryTree(TreeEntry &Entry, Value **In,
                                        Value **Out, Value **A,
                                        Value **Passthru, IRBuilder &Builder,
                                        TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadGuardedTempMaskMemory(In, A, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
    Value *S0B = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Out[0]);
    Value *S0 = Builder.CreateOr(S0A, S0B);
    if (S0) {
      Builder.CreateStore(VectorResult, Out[0]);
    }
    Value *S1A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
    Value *S1B = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Out[1]);
    Value *S1 = Builder.CreateOr(S1A, S1B);
    if (S1) {
      Builder.CreateStore(VectorResult, Out[1]);
    }
    Value *S2A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
    Value *S2B = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Out[2]);
    Value *S2 = Builder.CreateOr(S2A, S2B);
    if (S2) {
      Builder.CreateStore(VectorResult, Out[2]);
    }
    Value *S3A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
    Value *S3B = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Out[3]);
    Value *S3 = Builder.CreateOr(S3A, S3B);
    if (S3) {
      Builder.CreateStore(VectorResult, Out[3]);
    }
    replaceScalarUses(Entry, VectorResult);
  }
}
