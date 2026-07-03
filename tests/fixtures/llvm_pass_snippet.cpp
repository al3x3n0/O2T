namespace llvm {
struct Value {};
struct Instruction {};
struct TargetLibraryInfo {};
struct Terminator {};
struct UnreachableInst {};
struct SwitchInst {};
struct LoadInst {};
struct PHINode {};

struct BasicBlock {
  Terminator *getTerminator();
  BasicBlock *getSinglePredecessor();
};

struct AllocaInst {
  bool use_empty();
};

struct Loop {
  BasicBlock *getHeader();
  BasicBlock *getExitBlock();
};

namespace PatternMatch {
struct Pattern {};
Pattern m_Zero();
Pattern m_One();
bool match(Value *, Pattern);
} // namespace PatternMatch

template <typename T, typename U> bool isa(U *);
bool isInstructionTriviallyDead(Instruction *, const TargetLibraryInfo *);
bool isAllocaPromotable(AllocaInst *);
bool rewriteSingleStoreAlloca(AllocaInst *);
bool isRemovable(Instruction *);
bool isOverwrite(Instruction *);
bool FindAvailableLoadedValue(LoadInst *);
bool getSmallConstantTripCount(Loop *);
bool isLoopInvariant(Instruction *);
bool makeLoopInvariant(Instruction *);
bool isDeadLoopInstruction(Instruction *);
} // namespace llvm

using namespace llvm;
using namespace PatternMatch;

void instcombineLike(Value *Op0, Value *Op1, Instruction &I) {
  if (match(Op1, m_Zero())) {
    return;
  }
  if (match(Op1, m_One())) {
    return;
  }
  if (Op0 == Op1) {
    return;
  }
}

void dceLike(Instruction &I, const TargetLibraryInfo *TLI) {
  if (isInstructionTriviallyDead(&I, TLI)) {
    return;
  }
}

void simplifycfgLike(BasicBlock &BB) {
  if (isa<UnreachableInst>(BB.getTerminator())) {
    return;
  }
  if (BB.getSinglePredecessor()) {
    return;
  }
  if (isa<SwitchInst>(BB.getTerminator())) {
    return;
  }
}

void mem2regLike(AllocaInst &AI) {
  if (isAllocaPromotable(&AI)) {
    return;
  }
  if (rewriteSingleStoreAlloca(&AI)) {
    return;
  }
  if (AI.use_empty()) {
    return;
  }
}

void dseMemoryLike(Instruction &I) {
  if (isRemovable(&I)) {
    return;
  }
  if (isOverwrite(&I)) {
    return;
  }
}

void loadCombineLike(LoadInst &LI) {
  if (FindAvailableLoadedValue(&LI)) {
    return;
  }
}

void loopLike(Loop &L, Instruction &I, PHINode &Phi) {
  if (L.getHeader()) {
    return;
  }
  if (&Phi) {
    return;
  }
  if (getSmallConstantTripCount(&L)) {
    return;
  }
  if (isLoopInvariant(&I)) {
    return;
  }
  if (makeLoopInvariant(&I)) {
    return;
  }
  if (isDeadLoopInstruction(&I)) {
    return;
  }
  if (L.getExitBlock()) {
    return;
  }
}
