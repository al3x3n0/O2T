namespace llvm {
struct Loop {};
struct Instruction {
  void eraseFromParent();
};

bool isDeadLoopInstruction(Instruction *I);
void deleteDeadInstruction(Instruction *I);
} // namespace llvm

using namespace llvm;

void eraseDeadLoopInstruction(Loop &L, Instruction &I) {
  (void)L;
  if (isDeadLoopInstruction(&I))
    I.eraseFromParent();
}

void deleteDeadLoopInstruction(Loop *L, Instruction *I) {
  (void)L;
  if (isDeadLoopInstruction(I))
    deleteDeadInstruction(I);
}

void eraseLoopInstructionWithoutGuard(Loop &L, Instruction &I) {
  (void)L;
  I.eraseFromParent();
}

void notLoopDeletion(Loop &L, Instruction &I) {
  (void)L;
  if (isDeadLoopInstruction(&I))
    return;
}
