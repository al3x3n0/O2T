#include "o2t/GeneratorConfig.h"
#include "o2t/KleeCompat.h"
#include "o2t/ProbeBackend.h"
#include "o2t/PassInstrumentation.h"
#include "o2t/SymbolicConfig.h"

#if defined(O2T_KLEE_FEEDBACK) || defined(COMPILERVERIF_KLEE_FEEDBACK)
#include "o2t/GeneratedKleeFeedback.h"
#endif

#include <cstddef>

#if !(defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
#include "o2t/IRTextGenerator.h"

namespace {

bool generatedLooksLikeModule(const cv::GeneratedIR &generated) {
  return generated.moduleText.find("define i32 @test") != std::string::npos &&
         generated.moduleText.find("entry:") != std::string::npos &&
         generated.moduleText.find("ret i32") != std::string::npos;
}

} // namespace
#endif

int main() {
  cv::GeneratorConfig config = cv::makeSymbolicConfig();

  klee_assume(config.arithOpcode < 6);
  klee_assume(config.rhsMode < 4);
  klee_assume(config.extraOpcode < 6);
  klee_assume(config.predicate < 4);
  klee_assume(config.shape < 5);
  klee_assume(config.featureBits < 8);
  klee_assume(config.memoryShape < 6);
  klee_assume(config.pointerMode < 3);
  klee_assume(config.storeMode < 3);
  klee_assume(config.loadUseMode < 3);
  klee_assume(config.loopShape < 5);
  klee_assume(config.loopTripMode < 3);
  klee_assume(config.inductionMode < 3);
  klee_assume(config.loopUseMode < 3);
  klee_assume(config.vectorShape < 8);
  klee_assume(config.composeBits < 64);
  klee_assume(config.intWidth < 4);
  klee_assume(config.scalarArgs < 3);
  klee_assume(config.pointerArgs < 3);
  klee_assume(config.pointerNoalias < 2);
  klee_assume(config.castMode < 8);

#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
  const cv::ProbeBackendResult probeResult = cv::runProbeBackend(config);
  const cv::PassProbeCoverage coverage = probeResult.coverage;

#if defined(O2T_KLEE_FEEDBACK) || defined(COMPILERVERIF_KLEE_FEEDBACK)
  // Oracle-novelty feedback: prune paths whose config only re-derives markers
  // already covered by prior runs, steering KLEE toward the frontier.
  klee_assume(cv::isNovelCoverage(coverage));
#endif

  CV_PASS_PROBE_IF("probe.instcombine.add-zero", coverage.instcombineAddZero);
  CV_PASS_PROBE_IF("probe.instcombine.mul-one", coverage.instcombineMulOne);
  CV_PASS_PROBE_IF("probe.instcombine.xor-self", coverage.instcombineXorSelf);
  CV_PASS_PROBE_IF("probe.dce.dead-instruction", coverage.dceDeadInstruction);
  CV_PASS_PROBE_IF("probe.simplifycfg.unreachable-block",
                   coverage.simplifycfgUnreachableBlock);
  CV_PASS_PROBE_IF("probe.simplifycfg.diamond", coverage.simplifycfgDiamond);
  CV_PASS_PROBE_IF("probe.simplifycfg.nested-branch",
                   coverage.simplifycfgNestedBranch);
  CV_PASS_PROBE_IF("probe.simplifycfg.branch-chain",
                   coverage.simplifycfgBranchChain);
  CV_PASS_PROBE_IF("probe.mem2reg.promotable-alloca",
                   coverage.mem2regPromotableAlloca);
  CV_PASS_PROBE_IF("probe.mem2reg.store-load-forward",
                   coverage.mem2regStoreLoadForward);
  CV_PASS_PROBE_IF("probe.dse.dead-store", coverage.dseDeadStore);
  CV_PASS_PROBE_IF("probe.dse.overwritten-store",
                   coverage.dseOverwrittenStore);
  CV_PASS_PROBE_IF("probe.instcombine.redundant-load",
                   coverage.instcombineRedundantLoad);
  CV_PASS_PROBE_IF("probe.cleanup.unused-alloca",
                   coverage.cleanupUnusedAlloca);
  CV_PASS_PROBE_IF("probe.loop.canonical-header",
                   coverage.loopCanonicalHeader);
  CV_PASS_PROBE_IF("probe.loop.induction-phi", coverage.loopInductionPhi);
  CV_PASS_PROBE_IF("probe.loop.simple-trip-count",
                   coverage.loopSimpleTripCount);
  CV_PASS_PROBE_IF("probe.licm.invariant-op", coverage.licmInvariantOp);
  CV_PASS_PROBE_IF("probe.dce.dead-loop-instruction",
                   coverage.dceDeadLoopInstruction);
  CV_PASS_PROBE_IF("probe.simplifycfg.loop-exit",
                   coverage.simplifycfgLoopExit);
  CV_PASS_PROBE_IF("probe.vector.add-zero", coverage.vectorAddZero);
  CV_PASS_PROBE_IF("probe.vector.mul-one", coverage.vectorMulOne);
  CV_PASS_PROBE_IF("probe.vector.xor-self", coverage.vectorXorSelf);
  CV_PASS_PROBE_IF("probe.vector.shuffle-identity",
                   coverage.vectorShuffleIdentity);
  CV_PASS_PROBE_IF("probe.vector.shuffle-splat", coverage.vectorShuffleSplat);
  CV_PASS_PROBE_IF("probe.vector.extract-insert", coverage.vectorExtractInsert);
  CV_PASS_PROBE_IF("probe.vector.reduction-add-zero",
                   coverage.vectorReductionAddZero);

  klee_assert(probeResult.available);
#else
  const cv::GeneratedIR generated = cv::generateIR(config);
  const cv::ProbeBackendResult probeResult = cv::runProbeBackend(config);
  const cv::PassProbeCoverage coverage = probeResult.coverage;

  CV_PASS_PROBE_IF("probe.instcombine.add-zero", coverage.instcombineAddZero);
  CV_PASS_PROBE_IF("probe.instcombine.mul-one", coverage.instcombineMulOne);
  CV_PASS_PROBE_IF("probe.instcombine.xor-self", coverage.instcombineXorSelf);
  CV_PASS_PROBE_IF("probe.dce.dead-instruction", coverage.dceDeadInstruction);
  CV_PASS_PROBE_IF("probe.simplifycfg.unreachable-block",
                   coverage.simplifycfgUnreachableBlock);
  CV_PASS_PROBE_IF("probe.simplifycfg.diamond", coverage.simplifycfgDiamond);
  CV_PASS_PROBE_IF("probe.simplifycfg.nested-branch",
                   coverage.simplifycfgNestedBranch);
  CV_PASS_PROBE_IF("probe.simplifycfg.branch-chain",
                   coverage.simplifycfgBranchChain);
  CV_PASS_PROBE_IF("probe.mem2reg.promotable-alloca",
                   coverage.mem2regPromotableAlloca);
  CV_PASS_PROBE_IF("probe.mem2reg.store-load-forward",
                   coverage.mem2regStoreLoadForward);
  CV_PASS_PROBE_IF("probe.dse.dead-store", coverage.dseDeadStore);
  CV_PASS_PROBE_IF("probe.dse.overwritten-store",
                   coverage.dseOverwrittenStore);
  CV_PASS_PROBE_IF("probe.instcombine.redundant-load",
                   coverage.instcombineRedundantLoad);
  CV_PASS_PROBE_IF("probe.cleanup.unused-alloca",
                   coverage.cleanupUnusedAlloca);
  CV_PASS_PROBE_IF("probe.loop.canonical-header",
                   coverage.loopCanonicalHeader);
  CV_PASS_PROBE_IF("probe.loop.induction-phi", coverage.loopInductionPhi);
  CV_PASS_PROBE_IF("probe.loop.simple-trip-count",
                   coverage.loopSimpleTripCount);
  CV_PASS_PROBE_IF("probe.licm.invariant-op", coverage.licmInvariantOp);
  CV_PASS_PROBE_IF("probe.dce.dead-loop-instruction",
                   coverage.dceDeadLoopInstruction);
  CV_PASS_PROBE_IF("probe.simplifycfg.loop-exit",
                   coverage.simplifycfgLoopExit);
  CV_PASS_PROBE_IF("probe.vector.add-zero", coverage.vectorAddZero);
  CV_PASS_PROBE_IF("probe.vector.mul-one", coverage.vectorMulOne);
  CV_PASS_PROBE_IF("probe.vector.xor-self", coverage.vectorXorSelf);
  CV_PASS_PROBE_IF("probe.vector.shuffle-identity",
                   coverage.vectorShuffleIdentity);
  CV_PASS_PROBE_IF("probe.vector.shuffle-splat", coverage.vectorShuffleSplat);
  CV_PASS_PROBE_IF("probe.vector.extract-insert", coverage.vectorExtractInsert);
  CV_PASS_PROBE_IF("probe.vector.reduction-add-zero",
                   coverage.vectorReductionAddZero);

  klee_assert(generatedLooksLikeModule(generated));
  klee_assert(probeResult.available);
#endif
  return 0;
}
