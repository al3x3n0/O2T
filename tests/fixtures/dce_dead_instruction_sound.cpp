namespace llvm {
struct Instruction {
  void eraseFromParent();
};

bool isInstructionTriviallyDead(Instruction *I, void *TLI);
bool wouldInstructionBeTriviallyDead(Instruction *I);
} // namespace llvm

using namespace llvm;

void eraseTriviallyDeadOnly(Instruction *I) {
  if (isInstructionTriviallyDead(I, nullptr))
    I->eraseFromParent();
}

void eraseWouldBeDeadOnly(Instruction *I) {
  if (wouldInstructionBeTriviallyDead(I))
    I->eraseFromParent();
}
