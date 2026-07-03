namespace llvm {
struct Value {};
struct Instruction {
  void eraseFromParent();
};
struct Constant {
  static Value *getNullValue(int);
};
bool match(Value *, int);
int m_Zero();
int m_One();
bool hasPoisonGeneratingFlags(Instruction &);
bool isInstructionTriviallyDead(Instruction *, void *);
Value *replaceInstUsesWith(Instruction &, Value *);
} // namespace llvm

using namespace llvm;

Value *foldAddZero(Value *Op0, Value *Op1, Instruction &I) {
  if (match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *foldMulOne(Value *Op0, Value *Op1, Instruction &I) {
  if (match(Op1, m_One())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *foldXorSelf(Value *Op0, Value *Op1, Instruction &I) {
  if (Op0 == Op1) {
    return replaceInstUsesWith(I, Constant::getNullValue(0));
  }
  return Op0;
}

void removeDeadInstruction(Instruction &I) {
  if (isInstructionTriviallyDead(&I, nullptr)) {
    I.eraseFromParent();
  }
}

Value *guardedPoisonFold(Value *Op0, Value *Op1, Instruction &I) {
  if (!hasPoisonGeneratingFlags(I) && match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}

Value *missingRewrite(Value *Op1) {
  if (match(Op1, m_Zero())) {
  }
  return Op1;
}
