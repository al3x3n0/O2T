namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct CmpInst {
  enum Predicate { ICMP_EQ };
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

Value *makeStoreEqMask(Value *L, Value *R, IRBuilder &Builder) {
  return Builder.CreateICmp(CmpInst::ICMP_EQ, L, R);
}

Value *loadStoreSinkPack(Value **Base, Value **Cmp, Value **Passthru,
                         IRBuilder &Builder) {
  Value *M0 = makeStoreEqMask(Cmp[0], Passthru[0], Builder);
  Value *L0 = Builder.CreateMaskedLoad(Base[0], M0, Passthru[0]);
  Value *M1 = makeStoreEqMask(Cmp[1], Passthru[1], Builder);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], M1, Passthru[1]);
  Value *M2 = makeStoreEqMask(Cmp[2], Passthru[2], Builder);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], M2, Passthru[2]);
  Value *M3 = makeStoreEqMask(Cmp[3], Passthru[3], Builder);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], M3, Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void storeHelperSink(Value *VectorResult, Value **Out, Value **Cmp,
                     Value **Passthru, IRBuilder &Builder) {
  Value *S0 = makeStoreEqMask(Cmp[0], Passthru[0], Builder);
  Builder.CreateMaskedStore(VectorResult, Out[0], S0);
  Value *S1 = makeStoreEqMask(Cmp[1], Passthru[1], Builder);
  Builder.CreateMaskedStore(VectorResult, Out[1], S1);
  Value *S2 = makeStoreEqMask(Cmp[2], Passthru[2], Builder);
  Builder.CreateMaskedStore(VectorResult, Out[2], S2);
  Value *S3 = makeStoreEqMask(Cmp[3], Passthru[3], Builder);
  Builder.CreateMaskedStore(VectorResult, Out[3], S3);
}

void vectorizeHelperStoreSinkTree(TreeEntry &Entry, Value **In, Value **Out,
                                  Value **Cmp, Value **Passthru,
                                  IRBuilder &Builder,
                                  TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadStoreSinkPack(In, Cmp, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    storeHelperSink(VectorResult, Out, Cmp, Passthru, Builder);
    replaceScalarUses(Entry, VectorResult);
  }
}
