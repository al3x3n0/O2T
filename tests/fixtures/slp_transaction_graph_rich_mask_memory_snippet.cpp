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
  Value *CreateNot(Value *);
  Value *CreateSelect(Value *, Value *, Value *);
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

Value *loadRichMaskedMemory(Value **Base, Value **A, Value **Passthru,
                            IRBuilder &Builder) {
  Value *M0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *M0B = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Base[0]);
  Value *M0N = Builder.CreateNot(M0B);
  Value *M0 = Builder.CreateSelect(M0A, M0N, M0B);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
  Value *M1B = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Base[1]);
  Value *M1N = Builder.CreateNot(M1B);
  Value *M1 = Builder.CreateSelect(M1A, M1N, M1B);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
  Value *M2B = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Base[2]);
  Value *M2N = Builder.CreateNot(M2B);
  Value *M2 = Builder.CreateSelect(M2A, M2N, M2B);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
  Value *M3B = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Base[3]);
  Value *M3N = Builder.CreateNot(M3B);
  Value *M3 = Builder.CreateSelect(M3A, M3N, M3B);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeRichMaskMemoryTree(TreeEntry &Entry, Value **In, Value **Out,
                                 Value **A, Value **Passthru, IRBuilder &Builder,
                                 TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadRichMaskedMemory(In, A, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
    Value *S0B = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Out[0]);
    Value *S0N = Builder.CreateNot(S0B);
    Value *S0 = Builder.CreateSelect(S0A, S0N, S0B);
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
    Value *S1B = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Out[1]);
    Value *S1N = Builder.CreateNot(S1B);
    Value *S1 = Builder.CreateSelect(S1A, S1N, S1B);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
    Value *S2B = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Out[2]);
    Value *S2N = Builder.CreateNot(S2B);
    Value *S2 = Builder.CreateSelect(S2A, S2N, S2B);
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
    Value *S3B = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Out[3]);
    Value *S3N = Builder.CreateNot(S3B);
    Value *S3 = Builder.CreateSelect(S3A, S3N, S3B);
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
