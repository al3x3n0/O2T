namespace llvm {
struct UseRange {
  bool empty() const;
};
struct AllocaInst {
  bool use_empty() const;
  bool user_empty() const;
  bool hasNUses(unsigned N) const;
  bool hasNUsesOrMore(unsigned N) const;
  UseRange users() const;
  void eraseFromParent();
};
} // namespace llvm

using namespace llvm;

void eraseUnusedAlloca(AllocaInst *AI) {
  if (AI->use_empty())
    AI->eraseFromParent();
}

void eraseUserEmptyAlloca(AllocaInst &AI) {
  if (AI.user_empty())
    AI.eraseFromParent();
}

void eraseHasNUsesZeroAlloca(AllocaInst *AI) {
  if (AI->hasNUses( 0 ))
    AI->eraseFromParent();
}

void eraseUsersEmptyAlloca(AllocaInst &AI) {
  if (AI.users().empty())
    AI.eraseFromParent();
}

void eraseNotHasNUsesOrMoreAlloca(AllocaInst *AI) {
  if (!AI->hasNUsesOrMore( 1 ))
    AI->eraseFromParent();
}

void erasePositiveHasNUsesOrMoreAlloca(AllocaInst *AI) {
  if (AI->hasNUsesOrMore(1))
    AI->eraseFromParent();
}

void eraseAllocaWithoutGuard(AllocaInst *AI) {
  AI->eraseFromParent();
}

void notAllocaCleanup(AllocaInst *AI) {
  if (AI->use_empty())
    return;
}
