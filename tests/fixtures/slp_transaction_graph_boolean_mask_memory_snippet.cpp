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

Value *loadBooleanMaskedMemory(Value **Base, Value **A, Value **Passthru,
                               IRBuilder &Builder) {
  Value *M0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *M0B = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Base[0]);
  Value *M0 = Builder.CreateAnd(M0A, M0B);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
  Value *M1B = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Base[1]);
  Value *M1 = Builder.CreateAnd(M1A, M1B);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
  Value *M2B = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Base[2]);
  Value *M2 = Builder.CreateAnd(M2A, M2B);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
  Value *M3B = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Base[3]);
  Value *M3 = Builder.CreateAnd(M3A, M3B);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeBooleanMaskMemoryTree(TreeEntry &Entry, Value **In, Value **Out,
                                    Value **A, Value **Passthru, IRBuilder &Builder,
                                    TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadBooleanMaskedMemory(In, A, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
    Value *S0B = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Out[0]);
    Value *S0 = Builder.CreateOr(S0A, S0B);
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
    Value *S1B = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Out[1]);
    Value *S1 = Builder.CreateOr(S1A, S1B);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
    Value *S2B = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Out[2]);
    Value *S2 = Builder.CreateOr(S2A, S2B);
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
    Value *S3B = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Out[3]);
    Value *S3 = Builder.CreateOr(S3A, S3B);
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
