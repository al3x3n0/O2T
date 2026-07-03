#pragma once

#include "o2t/PassProbes.h"

#include <array>
#include <cstdint>

namespace cv {

struct ProbeMarkerMetadata {
  const char *marker;
  const char *group;
  const char *configPatchJson;
  bool PassProbeCoverage::*coverage;
};

inline constexpr std::array<ProbeMarkerMetadata, 49>
    kProbeMarkerMetadata{{
        {"probe.instcombine.add-zero", "scalar", "{\"arith_opcode\":0,\"rhs_mode\":0}", &PassProbeCoverage::instcombineAddZero},
        {"probe.instcombine.sub-zero", "scalar", "{\"arith_opcode\":1,\"rhs_mode\":0}", &PassProbeCoverage::instcombineSubZero},
        {"probe.instcombine.mul-one", "scalar", "{\"arith_opcode\":2,\"rhs_mode\":1}", &PassProbeCoverage::instcombineMulOne},
        {"probe.instcombine.xor-self", "scalar", "{\"extra_opcode\":3}", &PassProbeCoverage::instcombineXorSelf},
        {"probe.instcombine.or-zero", "scalar", "{\"arith_opcode\":4,\"rhs_mode\":0}", &PassProbeCoverage::instcombineOrZero},
        {"probe.instcombine.and-allones", "scalar", "{\"arith_opcode\":5,\"const_a\":-1,\"rhs_mode\":3}", &PassProbeCoverage::instcombineAndAllOnes},
        {"probe.instcombine.and-self", "scalar", "{\"extra_opcode\":5}", &PassProbeCoverage::instcombineAndSelf},
        {"probe.dce.dead-instruction", "scalar", "{\"extra_opcode\":4}", &PassProbeCoverage::dceDeadInstruction},
        {"probe.simplifycfg.unreachable-block", "cfg", "{\"shape\":3}", &PassProbeCoverage::simplifycfgUnreachableBlock},
        {"probe.simplifycfg.diamond", "cfg", "{\"shape\":1}", &PassProbeCoverage::simplifycfgDiamond},
        {"probe.simplifycfg.nested-branch", "cfg", "{\"shape\":2}", &PassProbeCoverage::simplifycfgNestedBranch},
        {"probe.simplifycfg.branch-chain", "cfg", "{\"shape\":4}", &PassProbeCoverage::simplifycfgBranchChain},
        {"probe.mem2reg.promotable-alloca", "memory", "{\"memory_shape\":1}", &PassProbeCoverage::mem2regPromotableAlloca},
        {"probe.mem2reg.store-load-forward", "memory", "{\"memory_shape\":1}", &PassProbeCoverage::mem2regStoreLoadForward},
        {"probe.dse.dead-store", "memory", "{\"memory_shape\":3}", &PassProbeCoverage::dseDeadStore},
        {"probe.dse.overwritten-store", "memory", "{\"memory_shape\":4}", &PassProbeCoverage::dseOverwrittenStore},
        {"probe.instcombine.redundant-load", "memory", "{\"memory_shape\":2}", &PassProbeCoverage::instcombineRedundantLoad},
        {"probe.cleanup.unused-alloca", "memory", "{\"memory_shape\":5}", &PassProbeCoverage::cleanupUnusedAlloca},
        {"probe.loop.canonical-header", "loop", "{\"loop_shape\":1}", &PassProbeCoverage::loopCanonicalHeader},
        {"probe.loop.induction-phi", "loop", "{\"loop_shape\":1}", &PassProbeCoverage::loopInductionPhi},
        {"probe.loop.simple-trip-count", "loop", "{\"loop_shape\":1}", &PassProbeCoverage::loopSimpleTripCount},
        {"probe.licm.invariant-op", "loop", "{\"loop_shape\":3}", &PassProbeCoverage::licmInvariantOp},
        {"probe.dce.dead-loop-instruction", "loop", "{\"loop_shape\":4}", &PassProbeCoverage::dceDeadLoopInstruction},
        {"probe.simplifycfg.loop-exit", "loop", "{\"loop_shape\":2}", &PassProbeCoverage::simplifycfgLoopExit},
        {"probe.globalopt.dead-initializer", "global", "{\"global_shape\":1}", &PassProbeCoverage::globalDeadInitializer},
        {"probe.vector.add-zero", "vector", "{\"vector_shape\":1}", &PassProbeCoverage::vectorAddZero},
        {"probe.vector.mul-one", "vector", "{\"vector_shape\":2}", &PassProbeCoverage::vectorMulOne},
        {"probe.vector.xor-self", "vector", "{\"vector_shape\":3}", &PassProbeCoverage::vectorXorSelf},
        {"probe.vector.shuffle-identity", "vector", "{\"vector_shape\":4}", &PassProbeCoverage::vectorShuffleIdentity},
        {"probe.vector.shuffle-splat", "vector", "{\"vector_shape\":5}", &PassProbeCoverage::vectorShuffleSplat},
        {"probe.vector.extract-insert", "vector", "{\"vector_shape\":6}", &PassProbeCoverage::vectorExtractInsert},
        {"probe.vector.reduction-add-zero", "vector", "{\"vector_shape\":7}", &PassProbeCoverage::vectorReductionAddZero},
        {"probe.vector.sub-zero", "vector", "{\"vector_shape\":8}", &PassProbeCoverage::vectorSubZero},
        {"probe.vector.or-zero", "vector", "{\"vector_shape\":9}", &PassProbeCoverage::vectorOrZero},
        {"probe.vector.and-allones", "vector", "{\"vector_shape\":10}", &PassProbeCoverage::vectorAndAllOnes},
        {"probe.vector.insert-extract-identity", "vector", "{\"vector_shape\":11}", &PassProbeCoverage::vectorInsertExtractIdentity},
        {"probe.vector.reduction-add-single-lane", "vector", "{\"vector_shape\":12}", &PassProbeCoverage::vectorReductionAddSingleLane},
        {"probe.vector.scalable.add-zero", "vector", "{\"vector_shape\":13}", &PassProbeCoverage::vectorScalableAddZero},
        {"probe.vector.scalable.mul-one", "vector", "{\"vector_shape\":14}", &PassProbeCoverage::vectorScalableMulOne},
        {"probe.vector.scalable.xor-self", "vector", "{\"vector_shape\":15}", &PassProbeCoverage::vectorScalableXorSelf},
        {"probe.vector.scalable.sub-zero", "vector", "{\"vector_shape\":16}", &PassProbeCoverage::vectorScalableSubZero},
        {"probe.vector.scalable.or-zero", "vector", "{\"vector_shape\":17}", &PassProbeCoverage::vectorScalableOrZero},
        {"probe.vector.scalable.and-allones", "vector", "{\"vector_shape\":18}", &PassProbeCoverage::vectorScalableAndAllOnes},
        {"probe.vector.scalable.reduction-add-zero", "vector", "{\"vector_shape\":19}", &PassProbeCoverage::vectorScalableReductionAddZero},
        {"probe.vector.smin", "vector", "{\"vector_shape\":20}", &PassProbeCoverage::vectorSMin},
        {"probe.vector.smax", "vector", "{\"vector_shape\":21}", &PassProbeCoverage::vectorSMax},
        {"probe.vector.umin", "vector", "{\"vector_shape\":22}", &PassProbeCoverage::vectorUMin},
        {"probe.vector.umax", "vector", "{\"vector_shape\":23}", &PassProbeCoverage::vectorUMax},
        {"probe.vector.abs", "vector", "{\"vector_shape\":24}", &PassProbeCoverage::vectorAbs},
    }};

} // namespace cv
