namespace llvm {
struct Instruction {
  void eraseFromParent();
};

bool isInstructionTriviallyDead(Instruction *I, void *TLI);
bool wouldInstructionBeTriviallyDead(Instruction *I);
void deleteDeadInstruction(Instruction *I);
void RecursivelyDeleteTriviallyDeadInstructions(Instruction *I, void *TLI);
} // namespace llvm

using namespace llvm;

void eraseTriviallyDead(Instruction *I) {
  if (isInstructionTriviallyDead(I, nullptr))
    I->eraseFromParent();
}

void eraseWouldBeDead(Instruction *I) {
  if (wouldInstructionBeTriviallyDead(I))
    I->eraseFromParent();
}

void eraseRecursiveDead(Instruction *I) {
  RecursivelyDeleteTriviallyDeadInstructions(I, nullptr);
}

void eraseWithoutGuard(Instruction *I) {
  I->eraseFromParent();
}

void notDeletion(Instruction *I) {
  (void)I;
}
