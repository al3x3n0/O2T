namespace llvm {
struct Instruction {};
struct LocationSize {
  static LocationSize precise(int);
  bool hasValue();
  int getValue();
};
struct SizeValue {
  bool hasValue();
  int getValue();
};
enum class AtomicOrdering {
  Unordered,
  Monotonic,
  Acquire,
  Release,
  AcquireRelease,
  SequentiallyConsistent
};
struct StoreInst : Instruction {
  bool isVolatile();
  bool isAtomic();
  AtomicOrdering getOrdering();
};
struct MemorySSA {};
struct AliasAnalysis {
  bool isNoAlias(Instruction *, Instruction *);
};

bool isRemovable(Instruction *);
bool isOverwrite(Instruction *);
bool getLocForWrite(Instruction *);
bool getMemoryAccess(MemorySSA *, Instruction *);
bool getClobberingMemoryAccess(MemorySSA *, Instruction *);
bool noInterveningStore(Instruction *, Instruction *);
bool noInterveningRead(Instruction *, Instruction *);
bool noInterveningMemoryAccess(Instruction *, Instruction *);
bool mayReadOrWriteMemory(Instruction *);
bool knownSizeWithinFourBytes(Instruction *);
bool knownSizeWithinEightBytes(Instruction *);
bool sameSize(Instruction *, Instruction *);
bool unknownSize(Instruction *);
SizeValue getOverwriteSize(Instruction *);
bool mayAlias(Instruction *, Instruction *);
bool fullyOverwrites(Instruction *, Instruction *);
bool fixedPartialOverwrite(Instruction *, Instruction *);
bool partialOverwriteByteMask(Instruction *, Instruction *, int, int);
bool partialOverwriteByteMask(Instruction *, Instruction *, int, int, int);
bool knownPartialOverwriteByteMask(Instruction *, Instruction *, int);
bool knownPartialOverwriteByteMask(Instruction *, Instruction *, int, int);
bool unknownAtomicOrdering(Instruction *);
} // namespace llvm

using namespace llvm;

void deadStoreWithMemorySSA(StoreInst &S, Instruction &Other, MemorySSA &MSSA,
                            AliasAnalysis &AA) {
  if (isRemovable(&S) && getMemoryAccess(&MSSA, &S) &&
      AA.isNoAlias(&S, &Other)) {
    return;
  }
}

void overwrittenStoreWithClobber(StoreInst &S, Instruction &Killing,
                                 MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      fullyOverwrites(&S, &Killing) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void fixedPartialOverwriteWithClobber(StoreInst &S, Instruction &Killing,
                                      MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      fixedPartialOverwrite(&S, &Killing) && !mayAlias(&S, &Killing)) {
    return;
  }
}

void highHalfPartialOverwriteWithClobber(StoreInst &S, Instruction &Killing,
                                         MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      partialOverwriteByteMask(&S, &Killing, 2, 2) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void sparsePartialOverwriteWithClobber(StoreInst &S, Instruction &Killing,
                                       MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      knownPartialOverwriteByteMask(&S, &Killing, 0x9) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void singleLanePartialOverwriteWithClobber(StoreInst &S, Instruction &Killing,
                                           MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      partialOverwriteByteMask(&S, &Killing, 2, 1) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void triplePartialOverwriteWithClobber(StoreInst &S, Instruction &Killing,
                                       MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      partialOverwriteByteMask(&S, &Killing, 0, 3) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void sparseTriplePartialOverwriteWithClobber(StoreInst &S,
                                             Instruction &Killing,
                                             MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      knownPartialOverwriteByteMask(&S, &Killing, 0xd) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void widthThreePartialOverwriteWithClobber(StoreInst &S, Instruction &Killing,
                                           MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinEightBytes(&S) &&
      partialOverwriteByteMask(&S, &Killing, 0, 1, 3) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void widthEightSparsePartialOverwriteWithClobber(StoreInst &S,
                                                 Instruction &Killing,
                                                 MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinEightBytes(&S) &&
      knownPartialOverwriteByteMask(&S, &Killing, 0x2a, 8) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void symbolicBoundedUnknownSizeOverwrite(StoreInst &S, Instruction &Killing,
                                         MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) && unknownSize(&S) &&
      sameSize(&S, &Killing) && knownSizeWithinEightBytes(&S) &&
      fullyOverwrites(&S, &Killing) && !mayAlias(&S, &Killing)) {
    return;
  }
}

void symbolicValueEqualityUnknownSizeOverwrite(StoreInst &S,
                                               Instruction &Killing,
                                               MemorySSA &MSSA) {
  SizeValue StoreSize = getOverwriteSize(&S);
  SizeValue KillingSize = getOverwriteSize(&Killing);
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) && unknownSize(&S) &&
      StoreSize.hasValue() && KillingSize.hasValue() &&
      StoreSize.getValue() == KillingSize.getValue() &&
      StoreSize.getValue() <= 8 && fullyOverwrites(&S, &Killing) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void symbolicFourByteUnknownSizeOverwrite(StoreInst &S, Instruction &Killing,
                                          MemorySSA &MSSA) {
  SizeValue StoreSize = getOverwriteSize(&S);
  SizeValue KillingSize = getOverwriteSize(&Killing);
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) && unknownSize(&S) &&
      StoreSize.hasValue() && KillingSize.hasValue() &&
      StoreSize.getValue() == KillingSize.getValue() &&
      StoreSize.getValue() <= 4 && fullyOverwrites(&S, &Killing) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void symbolicTooWideUnknownSizeOverwrite(StoreInst &S, Instruction &Killing,
                                         MemorySSA &MSSA) {
  SizeValue StoreSize = getOverwriteSize(&S);
  SizeValue KillingSize = getOverwriteSize(&Killing);
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) && unknownSize(&S) &&
      StoreSize.hasValue() && KillingSize.hasValue() &&
      StoreSize.getValue() == KillingSize.getValue() &&
      StoreSize.getValue() <= 16 && fullyOverwrites(&S, &Killing) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void locationSizeBoundedOverwrite(StoreInst &S, Instruction &Killing,
                                  MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      LocationSize::precise(8).hasValue() &&
      LocationSize::precise(8).getValue() <= 8 &&
      fullyOverwrites(&S, &Killing) && !mayAlias(&S, &Killing)) {
    return;
  }
}

void fullMaskPartialOverwriteBlocked(StoreInst &S, Instruction &Killing,
                                     MemorySSA &MSSA) {
  if (isOverwrite(&S) && getLocForWrite(&S) &&
      getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) &&
      noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      knownSizeWithinFourBytes(&S) &&
      knownPartialOverwriteByteMask(&S, &Killing, 0xf) &&
      !mayAlias(&S, &Killing)) {
    return;
  }
}

void volatileStoreBlocked(StoreInst &S, MemorySSA &MSSA) {
  if (isRemovable(&S) && getMemoryAccess(&MSSA, &S) && S.isVolatile()) {
    return;
  }
}

void unorderedAtomicStoreBlocked(StoreInst &S, MemorySSA &MSSA) {
  if (isRemovable(&S) && getMemoryAccess(&MSSA, &S) && S.isAtomic() &&
      S.getOrdering() == AtomicOrdering::Unordered) {
    return;
  }
}

void orderedAtomicStoreBlocked(StoreInst &S, MemorySSA &MSSA) {
  if (isRemovable(&S) && getMemoryAccess(&MSSA, &S) && S.isAtomic() &&
      S.getOrdering() == AtomicOrdering::SequentiallyConsistent) {
    return;
  }
}

void unknownAtomicOrderingStoreBlocked(StoreInst &S, MemorySSA &MSSA) {
  if (isRemovable(&S) && getMemoryAccess(&MSSA, &S) && S.isAtomic() &&
      unknownAtomicOrdering(&S)) {
    return;
  }
}

void unknownAliasOverwrite(StoreInst &S, Instruction &Killing, MemorySSA &MSSA) {
  if (isOverwrite(&S) && getClobberingMemoryAccess(&MSSA, &S) &&
      mayAlias(&S, &Killing)) {
    return;
  }
}

void interveningReadBlocked(StoreInst &S, Instruction &Killing,
                            MemorySSA &MSSA) {
  if (isOverwrite(&S) && getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) && fullyOverwrites(&S, &Killing)) {
    return;
  }
}

void unknownMemoryEffectBlocked(StoreInst &S, Instruction &Killing,
                                MemorySSA &MSSA, Instruction &Call) {
  if (isOverwrite(&S) && getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) && noInterveningRead(&S, &Killing) &&
      fullyOverwrites(&S, &Killing) && mayReadOrWriteMemory(&Call)) {
    return;
  }
}

void missingSizeOverwriteBlocked(StoreInst &S, Instruction &Killing,
                                 MemorySSA &MSSA) {
  if (isOverwrite(&S) && getClobberingMemoryAccess(&MSSA, &S) &&
      noInterveningStore(&S, &Killing) && noInterveningRead(&S, &Killing) &&
      noInterveningMemoryAccess(&S, &Killing) &&
      fullyOverwrites(&S, &Killing)) {
    return;
  }
}
