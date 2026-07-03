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

Value *loadBranchStoreMaskMemory(Value **Base, Value **Mask, Value **Passthru,
                                 IRBuilder &Builder) {
  Value *L0 = Builder.CreateMaskedLoad(Base[0], Mask[0], Passthru[0]);
  Value *L1 = Builder.CreateMaskedLoad(Base[1], Mask[1], Passthru[1]);
  Value *L2 = Builder.CreateMaskedLoad(Base[2], Mask[2], Passthru[2]);
  Value *L3 = Builder.CreateMaskedLoad(Base[3], Mask[3], Passthru[3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeBranchStoreMaskMemoryTree(TreeEntry &Entry, Value **In,
                                        Value **Out, Value **Mask,
                                        Value **Gate, Value **A,
                                        Value **Passthru, IRBuilder &Builder,
                                        TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadBranchStoreMaskMemory(In, Mask, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Value *S0T = Builder.CreateICmp(CmpInst::ICMP_EQ, A[0], Passthru[0]);
    Value *S0F = Builder.CreateICmp(CmpInst::ICMP_NE, A[0], Out[0]);
    Value *S0;
    if (Gate[0]) { S0 = S0T; } else { S0 = S0F; }
    Builder.CreateMaskedStore(VectorResult, Out[0], S0);
    Value *S1T = Builder.CreateICmp(CmpInst::ICMP_EQ, A[1], Passthru[1]);
    Value *S1F = Builder.CreateICmp(CmpInst::ICMP_NE, A[1], Out[1]);
    Value *S1;
    if (Gate[1]) { S1 = S1T; } else { S1 = S1F; }
    Builder.CreateMaskedStore(VectorResult, Out[1], S1);
    Value *S2T = Builder.CreateICmp(CmpInst::ICMP_EQ, A[2], Passthru[2]);
    Value *S2F = Builder.CreateICmp(CmpInst::ICMP_NE, A[2], Out[2]);
    Value *S2;
    if (Gate[2]) { S2 = S2T; } else { S2 = S2F; }
    Builder.CreateMaskedStore(VectorResult, Out[2], S2);
    Value *S3T = Builder.CreateICmp(CmpInst::ICMP_EQ, A[3], Passthru[3]);
    Value *S3F = Builder.CreateICmp(CmpInst::ICMP_NE, A[3], Out[3]);
    Value *S3;
    if (Gate[3]) { S3 = S3T; } else { S3 = S3F; }
    Builder.CreateMaskedStore(VectorResult, Out[3], S3);
    replaceScalarUses(Entry, VectorResult);
  }
}
