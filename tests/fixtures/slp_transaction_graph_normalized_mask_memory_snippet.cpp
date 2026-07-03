namespace llvm {
struct Value {};
struct Type {
  static Type *getInt1Ty();
};
struct ConstantInt {
  static Value *get(Type *, bool);
  static Value *getTrue(Type *);
  static Value *getFalse(Type *);
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
  Value *getTrue();
  Value *getFalse();
  Value *CreateICmp(CmpInst::Predicate, Value *, Value *);
  Value *CreateXor(Value *, Value *);
  Value *CreateMaskedLoad(Value *, Value *, Value *);
  void CreateMaskedStore(Value *, Value *, Value *);
  Value *CreateAdd(Value *, Value *);
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

Value *loadNormalizedMaskedMemory(Value **Base, Value **A, Value **Passthru,
                                  IRBuilder &Builder) {
  Value *TrueMask = Builder.getTrue();
  Value *FalseMask = ConstantInt::getFalse(Type::getInt1Ty());
  Value *M0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
  Value *M0 = Builder.CreateXor(M0A, TrueMask);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1A = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Base[1]);
  Value *M1 = Builder.CreateXor(FalseMask, M1A);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2 = ConstantInt::get(Type::getInt1Ty(), true);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3 = ConstantInt::getTrue(Type::getInt1Ty());
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeNormalizedMaskMemoryTree(TreeEntry &Entry, Value **In, Value **Out,
                                       Value **A, Value **Passthru,
                                       IRBuilder &Builder,
                                       TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadNormalizedMaskedMemory(In, A, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *TrueMask = Builder.getTrue();
    Value *FalseMask = Builder.getFalse();
    Value *S0A = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Out[0]);
    Value *S0 = Builder.CreateXor(TrueMask, S0A);
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1A = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Out[1]);
    Value *S1 = Builder.CreateXor(S1A, FalseMask);
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2 = ConstantInt::get(Type::getInt1Ty(), true);
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3 = ConstantInt::getTrue(Type::getInt1Ty());
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
