namespace llvm {
struct Instruction {};
struct MemoryAccess {};
struct MemoryLocation {};
struct StoreInst : Instruction {
  bool isVolatile();
  bool isAtomic();
};
struct MemorySSA {
  MemoryAccess *getMemoryAccess(Instruction *);
};
struct AliasAnalysis {
  bool isNoAlias(Instruction *, Instruction *);
};

bool isRemovable(Instruction *);
bool isOverwrite(Instruction *);
bool getLocForWrite(Instruction *, MemoryLocation &);
bool getClobberingMemoryAccess(MemorySSA &, Instruction *, MemoryAccess *&);
bool getDomMemoryDef(MemoryAccess *, MemoryAccess *);
bool noInterveningStore(Instruction *, Instruction *);
bool noInterveningRead(Instruction *, Instruction *);
bool noInterveningMemoryAccess(Instruction *, Instruction *);
bool fullyOverwrites(Instruction *, Instruction *);
bool partialOverwriteByteMask(Instruction *, Instruction *, int, int);
bool knownSizeWithinFourBytes(Instruction *);
bool mayReadOrWriteMemory(Instruction *);
bool mayAlias(Instruction *, Instruction *);
bool unknownSize(Instruction *);
bool nonOverlapping(Instruction *, Instruction *);
void DeleteDeadInstruction(Instruction *);

namespace {
struct DSEState {
  MemorySSA &MSSA;
  AliasAnalysis &AA;

  bool deleteDeadStore(StoreInst &DeadInst, Instruction &Other) {
    if (isRemovable(&DeadInst) && MSSA.getMemoryAccess(&DeadInst) &&
        AA.isNoAlias(&DeadInst, &Other)) {
      DeleteDeadInstruction(&DeadInst);
      return true;
    }
    return false;
  }

  bool eliminateRedundantStore(StoreInst &DeadInst, StoreInst &KillingI) {
    MemoryLocation DeadLoc;
    MemoryAccess *Current = nullptr;
    bool NoRead = noInterveningRead(&DeadInst, &KillingI);
    if (isOverwrite(&DeadInst) && getLocForWrite(&DeadInst, DeadLoc) &&
        getClobberingMemoryAccess(MSSA, &DeadInst, Current) &&
        getDomMemoryDef(Current, MSSA.getMemoryAccess(&KillingI)) &&
        noInterveningStore(&DeadInst, &KillingI) &&
        NoRead /* noInterveningRead */ &&
        noInterveningMemoryAccess(&DeadInst, &KillingI) &&
        knownSizeWithinFourBytes(&DeadInst) &&
        fullyOverwrites(&DeadInst, &KillingI) &&
        !mayAlias(&DeadInst, &KillingI)) {
      DeleteDeadInstruction(&DeadInst);
      return true;
    }
    return false;
  }

  bool eliminateMaskedStore(StoreInst &DeadInst, StoreInst &KillingI) {
    MemoryAccess *Current = nullptr;
    if (isOverwrite(&DeadInst) &&
        getClobberingMemoryAccess(MSSA, &DeadInst, Current) &&
        noInterveningStore(&DeadInst, &KillingI) &&
        noInterveningRead(&DeadInst, &KillingI) &&
        noInterveningMemoryAccess(&DeadInst, &KillingI) &&
        knownSizeWithinFourBytes(&DeadInst) &&
        partialOverwriteByteMask(&DeadInst, &KillingI, 2, 2)) {
      DeleteDeadInstruction(&DeadInst);
      return true;
    }
    return false;
  }

  bool rejectUnknownEffects(StoreInst &DeadInst, StoreInst &KillingI,
                            Instruction &MaybeCall) {
    MemoryAccess *Current = nullptr;
    if (isOverwrite(&DeadInst) &&
        getClobberingMemoryAccess(MSSA, &DeadInst, Current) &&
        noInterveningStore(&DeadInst, &KillingI) &&
        noInterveningRead(&DeadInst, &KillingI) &&
        fullyOverwrites(&DeadInst, &KillingI) &&
        mayReadOrWriteMemory(&MaybeCall)) {
      return false;
    }
    return true;
  }

  bool rejectMissingSize(StoreInst &DeadInst, StoreInst &KillingI) {
    MemoryAccess *Current = nullptr;
    if (isOverwrite(&DeadInst) &&
        getClobberingMemoryAccess(MSSA, &DeadInst, Current) &&
        noInterveningStore(&DeadInst, &KillingI) &&
        noInterveningRead(&DeadInst, &KillingI) &&
        noInterveningMemoryAccess(&DeadInst, &KillingI) &&
        fullyOverwrites(&DeadInst, &KillingI)) {
      return false;
    }
    return true;
  }

  bool rejectUnknownSize(StoreInst &DeadInst, StoreInst &KillingI) {
    MemoryAccess *Current = nullptr;
    if (isOverwrite(&DeadInst) &&
        getClobberingMemoryAccess(MSSA, &DeadInst, Current) &&
        unknownSize(&DeadInst)) {
      return false;
    }
    return true;
  }

  bool rejectNonOverlap(StoreInst &DeadInst, StoreInst &KillingI) {
    MemoryAccess *Current = nullptr;
    if (isOverwrite(&DeadInst) &&
        getClobberingMemoryAccess(MSSA, &DeadInst, Current) &&
        nonOverlapping(&DeadInst, &KillingI)) {
      return false;
    }
    return true;
  }
};
} // namespace

__attribute__((used)) bool runDSEUpstreamLike(DSEState &State,
                                              StoreInst &DeadInst,
                                              StoreInst &KillingI,
                                              Instruction &Other) {
  bool Changed = false;
  Changed |= State.deleteDeadStore(DeadInst, Other);
  Changed |= State.eliminateRedundantStore(DeadInst, KillingI);
  Changed |= State.eliminateMaskedStore(DeadInst, KillingI);
  Changed |= !State.rejectUnknownEffects(DeadInst, KillingI, Other);
  Changed |= !State.rejectMissingSize(DeadInst, KillingI);
  Changed |= !State.rejectUnknownSize(DeadInst, KillingI);
  Changed |= !State.rejectNonOverlap(DeadInst, KillingI);
  return Changed;
}
} // namespace llvm
