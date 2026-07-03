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

Value *loadAliasMaskMemory(Value **Base, Value **A, Value **Passthru,
                           IRBuilder &Builder) {
  Value *C0 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *D0 = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Base[0]);
  Value *M0A = C0;
  Value *M0 = Builder.CreateAnd(M0A, D0);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *C1 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
  Value *M1 = C1;
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *C2 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
  Value *M2A = C2;
  Value *M2 = M2A;
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *C3 = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
  Value *D3 = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Base[3]);
  Value *M3A = C3;
  Value *M3 = Builder.CreateOr(M3A, D3);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeAliasMaskMemoryTree(TreeEntry &Entry, Value **In, Value **Out,
                                  Value **A, Value **Passthru,
                                  IRBuilder &Builder, TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadAliasMaskMemory(In, A, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0C = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
    Value *S0 = S0C;
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1C = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
    Value *S1D = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Out[1]);
    Value *S1A = S1C;
    Value *S1 = Builder.CreateAnd(S1A, S1D);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2C = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
    Value *S2 = S2C;
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3C = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
    Value *S3 = S3C;
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
