// A deliberately family-ambiguous pass snippet: it touches no classifier signal strongly enough
// to clear the retention threshold, so the deterministic orchestrator reports it UNCLASSIFIED --
// exactly the residue the verification agent exists to triage.
#include <vector>

namespace vendor {

struct PassContext;

// Vendor-internal bookkeeping; deliberately no recognizable LLVM transform idioms.
bool prepareBookkeeping(PassContext &ctx, std::vector<int> &slots) {
  int budget = 0;
  for (int slot : slots)
    budget += slot;
  return budget > 0;
}

}  // namespace vendor
