namespace llvm {
struct Value {};
struct Instruction {
  enum BinaryOps { Add, Sub, Mul, Xor, Or, And };
};
struct TreeEntry {
  Value *Scalars[4];
};
struct IRBuilder {
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

constexpr unsigned Addr0 = 0;
enum { Addr1 = Addr0 + 2, Addr2 = Addr1 + 2, Addr3 = Addr2 + 2 };
static constexpr int Pass0 = 0;
enum { Pass1 = Pass0 + 1, Pass2 = Pass1 + 1, Pass3 = Pass2 + 1 };
constexpr unsigned MaskLane0 = 0;
enum { MaskLane1 = MaskLane0 + 1, MaskLane2 = MaskLane1 + 1, MaskLane3 = MaskLane2 + 1 };

Value *loadStaticMemoryIndex(Value **Base, Value **Mask, Value **Passthru,
                             IRBuilder &Builder) {
  Value *L0 = Builder.CreateMaskedLoad(Base[Addr0 + 0], Mask[MaskLane0 | 0], Passthru[(Pass0)]);
  Value *L1 = Builder.CreateMaskedLoad(Base[Addr1], Mask[MaskLane1], Passthru[Pass1]);
  Value *L2 = Builder.CreateMaskedLoad(Base[(Addr2)], Mask[MaskLane2], Passthru[Pass2]);
  Value *L3 = Builder.CreateMaskedLoad(Base[Addr3], Mask[MaskLane3], Passthru[Pass3]);
  return buildPack(L0, L1, L2, L3);
}

void vectorizeStaticMemoryIndexTree(TreeEntry &Entry, Value **In, Value **Out,
                                    Value **Mask, Value **Passthru,
                                    IRBuilder &Builder,
                                    TargetTransformInfo &TTI) {
  if (allSameOpcode(Entry, Instruction::Add) && isValidElementType(Entry) &&
      noAlias(In, Out) && isProfitable(Entry, TTI)) {
    Value *LHS = loadStaticMemoryIndex(In, Mask, Passthru, Builder);
    Value *RHS = packOperand(Entry, 1);
    Value *VectorAdd = Builder.CreateAdd(LHS, RHS);
    Value *VectorResult = Builder.CreateXor(VectorAdd, RHS);
    Builder.CreateMaskedStore(VectorResult, Out[Addr0], Mask[MaskLane0]);
    Builder.CreateMaskedStore(VectorResult, Out[Addr1], Mask[MaskLane1]);
    Builder.CreateMaskedStore(VectorResult, Out[Addr2], Mask[MaskLane2]);
    Builder.CreateMaskedStore(VectorResult, Out[Addr3], Mask[MaskLane3]);
    replaceScalarUses(Entry, VectorResult);
  }
}
