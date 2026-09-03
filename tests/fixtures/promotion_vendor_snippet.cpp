// A vendor promotion-like pass, used to pin the STRUCTURAL form of the attribution bug.
//
// The `promotion` family has exactly ONE strategy, `mem2reg-ir`, a pass-runner carrying
// `canonical_pass=mem2reg`. For any pass that is not itself mem2reg, that check falls back to
// running canonical Mem2Reg on canonical IR -- so the family has NO check capable of saying
// anything about a vendor pass, and every such pass was certified `proved` by a proof about LLVM's
// own mem2reg. Not a corner case: 100% of vendor passes landing in this family, unconditionally.
//
// The peephole case (cross_family_unattributed_snippet.cpp) needed a coincidence -- its one
// source-targeted check happening to answer `inconclusive`. This one needs nothing to go wrong.
namespace llvm {
struct AllocaInst {};
struct Function {};
bool isAllocaPromotable(const AllocaInst *);
void PromoteMemToReg(AllocaInst *, Function &);
} // namespace llvm
using namespace llvm;

// Deliberately WRONG: promotes without checking promotability.
void promoteAll(AllocaInst *AI, Function &F) {
  PromoteMemToReg(AI, F);
}
