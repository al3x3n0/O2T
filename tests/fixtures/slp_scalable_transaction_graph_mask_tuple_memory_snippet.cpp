namespace llvm {
struct Value {};
struct ElementCount {
  static ElementCount getScalable(unsigned);
};
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
  Value *CreateNot(Value *);
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

Value *loadScalableTupleMaskedMemory(Value **Base, Value **Cmp,
                                     Value **Passthru, IRBuilder &Builder) {
  Value *M0 = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[0], Passthru[0]);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1 = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[1], Passthru[1]);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[2], Passthru[2]);
  Value *M2B = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[2], Base[2]);
  Value *M2 = Builder.CreateAnd(M2A, M2B);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[3], Passthru[3]);
  Value *M3 = Builder.CreateNot(M3A);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeScalableTupleMaskedMemoryTree(
    TreeEntry &Entry, Value **In, Value **Out, Value **Cmp, Value **Passthru,
    IRBuilder &Builder, TargetTransformInfo &TTI) {
  auto EC = ElementCount::getScalable(4);
  (void)EC;
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadScalableTupleMaskedMemory(In, Cmp, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0 = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[0], Passthru[0]);
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1 = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[1], Passthru[1]);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[2], Passthru[2]);
    Value *S2B = Builder.CreateICmp(CmpInst::ICMP_NE, Cmp[2], In[2]);
    Value *S2 = Builder.CreateAnd(S2A, S2B);
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3A = Builder.CreateICmp(CmpInst::ICMP_EQ, Cmp[3], Passthru[3]);
    Value *S3 = Builder.CreateNot(S3A);
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
