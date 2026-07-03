// DSE-style memory transforms, mined to a theory-of-arrays before/after op sequence and
// discharged: the fold is sound iff its OWN legality guards (overwrite / no-alias / no
// intervening access) suffice. A fold that removes/forwards without the needed guard is
// refuted with a concrete colliding-address witness.
namespace llvm {
struct Instruction {};
struct StoreInst : Instruction {};
struct LoadInst : Instruction {};
struct Value {};
bool isOverwrite(Instruction *, Instruction *);
bool fullyOverwrites(Instruction *, Instruction *);
bool noInterveningRead(Instruction *, Instruction *);
bool noInterveningStore(Instruction *, Instruction *);
bool isNoAlias(Instruction *, Instruction *);
void deleteDeadInstruction(Instruction *);
Value *storedValue(StoreInst *);
void replaceAllUsesWith(LoadInst *, Value *);
} // namespace llvm

using namespace llvm;

// SOUND: remove a dead store fully overwritten by a later store to the same location, with
// no intervening read/store. The overwrite guard establishes the addresses alias.
bool eliminateOverwrittenStore(StoreInst &Dead, StoreInst &Killing) {
  if (isOverwrite(&Dead, &Killing) && fullyOverwrites(&Dead, &Killing) &&
      noInterveningRead(&Dead, &Killing) && noInterveningStore(&Dead, &Killing)) {
    deleteDeadInstruction(&Dead);
    return true;
  }
  return false;
}

// SOUND: forward a store's value to a later load of the same pointer across a store to a
// provably-non-aliasing location.
bool forwardStoreToLoad(StoreInst &Store, StoreInst &Other, LoadInst &Load) {
  if (noInterveningRead(&Store, &Load) && isNoAlias(&Other, &Store)) {
    replaceAllUsesWith(&Load, storedValue(&Store));
    return true;
  }
  return false;
}

// UNSOUND (planted): forwards a store's value across another store WITHOUT proving the other
// store is non-aliasing. If Other aliases Store, the load observes Other's value, not Store's.
bool forwardStoreToLoadNoAliasMissing(StoreInst &Store, StoreInst &Other, LoadInst &Load) {
  if (noInterveningRead(&Store, &Load)) {
    replaceAllUsesWith(&Load, storedValue(&Store));
    return true;
  }
  return false;
}

// UNSOUND (planted): removes a store guarded only by no-intervening-read -- WITHOUT establishing
// that a later store overwrites it. Removing it can change the final memory; must be REFUTED.
bool eliminateStoreNoOverwriteGuard(StoreInst &Dead, StoreInst &Killing) {
  if (noInterveningRead(&Dead, &Killing) && noInterveningStore(&Dead, &Killing)) {
    deleteDeadInstruction(&Dead);
    return true;
  }
  return false;
}
