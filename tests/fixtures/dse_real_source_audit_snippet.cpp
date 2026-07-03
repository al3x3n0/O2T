namespace llvm {
struct Instruction {};
struct MemoryAccess {};
struct MemoryDef : MemoryAccess {};
struct MemoryLocation {};
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
bool mayReadOrWriteMemory(Instruction *);
bool knownSizeWithinFourBytes(Instruction *);
bool knownSizeWithinEightBytes(Instruction *);
bool sameSize(Instruction *, Instruction *);
SizeValue getOverwriteSize(Instruction *);
bool mayAlias(Instruction *, Instruction *);
bool fullyOverwrites(Instruction *, Instruction *);
bool isPartialOverwrite(Instruction *, Instruction *);
bool fixedPartialOverwrite(Instruction *, Instruction *);
bool partialOverwriteByteMask(Instruction *, Instruction *, int, int);
bool partialOverwriteByteMask(Instruction *, Instruction *, int, int, int);
bool knownPartialOverwriteByteMask(Instruction *, Instruction *, int);
bool knownPartialOverwriteByteMask(Instruction *, Instruction *, int, int);
bool unknownAtomicOrdering(Instruction *);
bool nonOverlapping(Instruction *, Instruction *);
bool unknownSize(Instruction *);
void deleteDeadInstruction(Instruction *);
} // namespace llvm

using namespace llvm;

namespace {
struct DSEState {
  MemorySSA &MSSA;
  AliasAnalysis &AA;

  bool eraseDeadStore(StoreInst &Store, Instruction &Other) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        AA.isNoAlias(&Store, &Other)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseOverwrittenStore(StoreInst &Store, StoreInst &KillingStore) {
    MemoryLocation StoreLoc;
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) && getLocForWrite(&Store, StoreLoc) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        getDomMemoryDef(Clobber, MSSA.getMemoryAccess(&KillingStore)) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool keepSideEffectingStore(StoreInst &Store) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        (Store.isVolatile() || Store.isAtomic())) {
      return false;
    }
    return true;
  }

  bool keepVolatileStore(StoreInst &Store) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        Store.isVolatile()) {
      return false;
    }
    return true;
  }

  bool keepUnorderedAtomicStore(StoreInst &Store) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        Store.isAtomic() && Store.getOrdering() == AtomicOrdering::Unordered) {
      return false;
    }
    return true;
  }

  bool keepOrderedAtomicStore(StoreInst &Store) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        Store.isAtomic() &&
        Store.getOrdering() == AtomicOrdering::SequentiallyConsistent) {
      return false;
    }
    return true;
  }

  bool keepUnknownOrderingAtomicStore(StoreInst &Store) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        Store.isAtomic() && unknownAtomicOrdering(&Store)) {
      return false;
    }
    return true;
  }

  bool rejectAmbiguousOverwrite(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        mayAlias(&Store, &KillingStore)) {
      return false;
    }
    return true;
  }

  bool rejectPartialOverwrite(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        isPartialOverwrite(&Store, &KillingStore)) {
      return false;
    }
    return true;
  }

  bool eraseFixedPartialOverwrite(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        fixedPartialOverwrite(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseHighHalfPartialOverwrite(StoreInst &Store,
                                     StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        partialOverwriteByteMask(&Store, &KillingStore, 2, 2)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseSparsePartialOverwrite(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        knownPartialOverwriteByteMask(&Store, &KillingStore, 0x9)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseSingleLanePartialOverwrite(StoreInst &Store,
                                       StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        partialOverwriteByteMask(&Store, &KillingStore, 2, 1)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseTriplePartialOverwrite(StoreInst &Store,
                                   StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        partialOverwriteByteMask(&Store, &KillingStore, 0, 3)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseSparseTriplePartialOverwrite(StoreInst &Store,
                                         StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        knownPartialOverwriteByteMask(&Store, &KillingStore, 0xd)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseWidthThreePartialOverwrite(StoreInst &Store,
                                       StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinEightBytes(&Store) &&
        partialOverwriteByteMask(&Store, &KillingStore, 0, 1, 3)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseWidthEightSparsePartialOverwrite(StoreInst &Store,
                                             StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinEightBytes(&Store) &&
        knownPartialOverwriteByteMask(&Store, &KillingStore, 0x2a, 8)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool rejectFullMaskPartialOverwrite(StoreInst &Store,
                                      StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        knownPartialOverwriteByteMask(&Store, &KillingStore, 0xf)) {
      return false;
    }
    return true;
  }

  bool rejectNonOverlappingOverwrite(StoreInst &Store,
                                     StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        nonOverlapping(&Store, &KillingStore)) {
      return false;
    }
    return true;
  }

  bool rejectUnknownSizeOverwrite(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        unknownSize(&Store)) {
      return false;
    }
    return true;
  }

  bool eraseSymbolicBoundedUnknownSize(StoreInst &Store,
                                       StoreInst &KillingStore) {
    MemoryLocation StoreLoc;
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) && getLocForWrite(&Store, StoreLoc) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        unknownSize(&Store) && sameSize(&Store, &KillingStore) &&
        knownSizeWithinEightBytes(&Store) &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseSymbolicValueEqualityUnknownSize(StoreInst &Store,
                                             StoreInst &KillingStore) {
    MemoryLocation StoreLoc;
    MemoryAccess *Clobber = nullptr;
    SizeValue StoreSize = getOverwriteSize(&Store);
    SizeValue KillingSize = getOverwriteSize(&KillingStore);
    if (isOverwrite(&Store) && getLocForWrite(&Store, StoreLoc) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        unknownSize(&Store) && StoreSize.hasValue() &&
        KillingSize.hasValue() &&
        StoreSize.getValue() == KillingSize.getValue() &&
        StoreSize.getValue() <= 8 &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool eraseSymbolicFourByteUnknownSize(StoreInst &Store,
                                        StoreInst &KillingStore) {
    MemoryLocation StoreLoc;
    MemoryAccess *Clobber = nullptr;
    SizeValue StoreSize = getOverwriteSize(&Store);
    SizeValue KillingSize = getOverwriteSize(&KillingStore);
    if (isOverwrite(&Store) && getLocForWrite(&Store, StoreLoc) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        unknownSize(&Store) && StoreSize.hasValue() &&
        KillingSize.hasValue() &&
        StoreSize.getValue() == KillingSize.getValue() &&
        StoreSize.getValue() <= 4 &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool rejectTooWideSymbolicUnknownSize(StoreInst &Store,
                                        StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    SizeValue StoreSize = getOverwriteSize(&Store);
    SizeValue KillingSize = getOverwriteSize(&KillingStore);
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        unknownSize(&Store) && StoreSize.hasValue() &&
        KillingSize.hasValue() &&
        StoreSize.getValue() == KillingSize.getValue() &&
        StoreSize.getValue() <= 16 &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      return false;
    }
    return true;
  }

  bool eraseLocationSizeBoundedStore(StoreInst &Store,
                                     StoreInst &KillingStore) {
    MemoryLocation StoreLoc;
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) && getLocForWrite(&Store, StoreLoc) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        LocationSize::precise(8).hasValue() &&
        LocationSize::precise(8).getValue() <= 8 &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool rejectInterveningRead(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        fullyOverwrites(&Store, &KillingStore)) {
      return false;
    }
    return true;
  }

  bool rejectUnknownMemoryEffect(StoreInst &Store, StoreInst &KillingStore,
                                 Instruction &Call) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        fullyOverwrites(&Store, &KillingStore) &&
        mayReadOrWriteMemory(&Call)) {
      return false;
    }
    return true;
  }

  bool rejectMissingSize(StoreInst &Store, StoreInst &KillingStore) {
    MemoryAccess *Clobber = nullptr;
    if (isOverwrite(&Store) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        noInterveningStore(&Store, &KillingStore) &&
        noInterveningRead(&Store, &KillingStore) &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        fullyOverwrites(&Store, &KillingStore)) {
      return false;
    }
    return true;
  }
};
} // namespace

__attribute__((used)) bool runDSEAuditFixture(DSEState &State, StoreInst &Store,
                                              StoreInst &KillingStore,
                                              Instruction &Other) {
  bool Changed = false;
  Changed |= State.eraseDeadStore(Store, Other);
  Changed |= State.eraseOverwrittenStore(Store, KillingStore);
  Changed |= State.eraseFixedPartialOverwrite(Store, KillingStore);
  Changed |= State.eraseHighHalfPartialOverwrite(Store, KillingStore);
  Changed |= State.eraseSparsePartialOverwrite(Store, KillingStore);
  Changed |= State.eraseSingleLanePartialOverwrite(Store, KillingStore);
  Changed |= State.eraseTriplePartialOverwrite(Store, KillingStore);
  Changed |= State.eraseSparseTriplePartialOverwrite(Store, KillingStore);
  Changed |= State.eraseWidthThreePartialOverwrite(Store, KillingStore);
  Changed |= State.eraseWidthEightSparsePartialOverwrite(Store, KillingStore);
  Changed |= !State.keepSideEffectingStore(Store);
  Changed |= !State.keepVolatileStore(Store);
  Changed |= !State.keepUnorderedAtomicStore(Store);
  Changed |= !State.keepOrderedAtomicStore(Store);
  Changed |= !State.keepUnknownOrderingAtomicStore(Store);
  Changed |= !State.rejectAmbiguousOverwrite(Store, KillingStore);
  Changed |= !State.rejectPartialOverwrite(Store, KillingStore);
  Changed |= !State.rejectNonOverlappingOverwrite(Store, KillingStore);
  Changed |= !State.rejectUnknownSizeOverwrite(Store, KillingStore);
  Changed |= State.eraseSymbolicBoundedUnknownSize(Store, KillingStore);
  Changed |= State.eraseSymbolicValueEqualityUnknownSize(Store, KillingStore);
  Changed |= State.eraseSymbolicFourByteUnknownSize(Store, KillingStore);
  Changed |= !State.rejectTooWideSymbolicUnknownSize(Store, KillingStore);
  Changed |= State.eraseLocationSizeBoundedStore(Store, KillingStore);
  Changed |= !State.rejectInterveningRead(Store, KillingStore);
  Changed |= !State.rejectUnknownMemoryEffect(Store, KillingStore, Other);
  Changed |= !State.rejectMissingSize(Store, KillingStore);
  Changed |= !State.rejectFullMaskPartialOverwrite(Store, KillingStore);
  return Changed;
}
