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

Value *loadGeneralizedMaskSyntaxMemory(Value **Base, Value **Cmp,
                                       Value **Passthru,
                                       IRBuilder *Builder) {
  auto *M0A = Builder->CreateICmp(CmpInst::ICMP_EQ, Cmp[0], Passthru[0]);
  Value *const M0B = Builder->CreateICmp(CmpInst::ICMP_NE, Cmp[0], Base[0]);
  auto *M0 = Builder->CreateAnd(M0A, M0B);
  Value *L0 = Builder->CreateMaskedLoad(Base[0], M0, Passthru[0]);
  auto *M1A = Builder->CreateICmp(CmpInst::ICMP_EQ, Cmp[1], Passthru[1]);
  auto *M1B = Builder->CreateICmp(CmpInst::ICMP_NE, Cmp[1], Base[1]);
  auto *M1N = Builder->CreateNot(M1B);
  auto *M1 = Builder->CreateSelect(M1A, M1N, M1B);
  Value *L1 = Builder->CreateMaskedLoad(Base[1], M1, Passthru[1]);
  auto *M2A = Builder->CreateICmp(CmpInst::ICMP_EQ, Cmp[2], Passthru[2]);
  auto *M2B = Builder->CreateICmp(CmpInst::ICMP_NE, Cmp[2], Base[2]);
  auto *M2 = Builder->CreateOr(M2A, M2B);
  Value *L2 = Builder->CreateMaskedLoad(Base[2], M2, Passthru[2]);
  auto *M3A = Builder->CreateICmp(CmpInst::ICMP_EQ, Cmp[3], Passthru[3]);
  auto *M3B = Builder->CreateICmp(CmpInst::ICMP_NE, Cmp[3], Base[3]);
  auto *M3 = Builder->CreateAnd(M3A, M3B);
  Value *L3 = Builder->CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeGeneralizedMaskSyntaxMemoryTree(TreeEntry &Entry, Value **In,
                                              Value **Out, Value **Cmp,
                                              Value **Passthru,
                                              IRBuilder &Builder,
                                              TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    IRBuilder *MaskBuilder = &Builder;
    Value *LHS = loadGeneralizedMaskSyntaxMemory(In, Cmp, Passthru, MaskBuilder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    auto *S0A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[0], Passthru[0]);
    Value *const S0B = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[0], Out[0]);
    auto *S0 = Builder.CreateOr(S0A, S0B);
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    auto *S1A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[1], Passthru[1]);
    auto *S1B = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[1], Out[1]);
    auto *S1N = Builder.CreateNot(S1B);
    auto *S1 = Builder.CreateSelect(S1A, S1N, S1B);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    auto *S2A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[2], Passthru[2]);
    auto *S2B = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[2], Out[2]);
    auto *S2 = Builder.CreateAnd(S2A, S2B);
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    auto *S3A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[3], Passthru[3]);
    auto *S3B = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[3], Out[3]);
    auto *S3 = Builder.CreateOr(S3A, S3B);
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
