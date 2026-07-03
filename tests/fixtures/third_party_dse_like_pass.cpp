namespace vendor_dse {
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
bool knownSizeWithinFourBytes(Instruction *);
bool mayAlias(Instruction *, Instruction *);
bool fullyOverwrites(Instruction *, Instruction *);
bool isPartialOverwrite(Instruction *, Instruction *);
void deleteDeadInstruction(Instruction *);
} // namespace llvm

using namespace llvm;

struct VendorDSE {
  MemorySSA &MSSA;
  AliasAnalysis &AA;

  bool removeUnusedStore(StoreInst &Store, Instruction &Other) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        AA.isNoAlias(&Store, &Other)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool removeOverwrittenStore(StoreInst &Store, StoreInst &KillingStore) {
    MemoryLocation Loc;
    MemoryAccess *Clobber = nullptr;
    bool NoRead = noInterveningRead(&Store, &KillingStore);
    if (isOverwrite(&Store) && getLocForWrite(&Store, Loc) &&
        getClobberingMemoryAccess(MSSA, &Store, Clobber) &&
        getDomMemoryDef(Clobber, MSSA.getMemoryAccess(&KillingStore)) &&
        noInterveningStore(&Store, &KillingStore) &&
        NoRead /* noInterveningRead */ &&
        noInterveningMemoryAccess(&Store, &KillingStore) &&
        knownSizeWithinFourBytes(&Store) &&
        fullyOverwrites(&Store, &KillingStore) &&
        !mayAlias(&Store, &KillingStore)) {
      deleteDeadInstruction(&Store);
      return true;
    }
    return false;
  }

  bool keepOrderedStore(StoreInst &Store) {
    if (isRemovable(&Store) && MSSA.getMemoryAccess(&Store) &&
        (Store.isVolatile() || Store.isAtomic())) {
      return false;
    }
    return true;
  }

  bool rejectMayAliasOverwrite(StoreInst &Store, StoreInst &KillingStore) {
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
};

__attribute__((used)) bool runVendorDSE(VendorDSE &Pass, StoreInst &Store,
                                        StoreInst &KillingStore,
                                        Instruction &Other) {
  bool Changed = false;
  Changed |= Pass.removeUnusedStore(Store, Other);
  Changed |= Pass.removeOverwrittenStore(Store, KillingStore);
  Changed |= !Pass.keepOrderedStore(Store);
  Changed |= !Pass.rejectMayAliasOverwrite(Store, KillingStore);
  Changed |= !Pass.rejectPartialOverwrite(Store, KillingStore);
  return Changed;
}
} // namespace vendor_dse
