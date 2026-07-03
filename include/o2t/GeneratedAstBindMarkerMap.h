#pragma once

#include <array>

namespace cv {

struct AstBindMarkerMetadata {
  const char *bindName;
  const char *marker;
};

inline constexpr std::array<AstBindMarkerMetadata, 48>
    kAstBindMarkerMetadata{{
        {"vector-add-zero", "probe.vector.add-zero"},
        {"vector-mul-one", "probe.vector.mul-one"},
        {"vector-xor-self", "probe.vector.xor-self"},
        {"vector-shuffle-identity", "probe.vector.shuffle-identity"},
        {"vector-shuffle-splat", "probe.vector.shuffle-splat"},
        {"vector-extract-insert", "probe.vector.extract-insert"},
        {"vector-reduction-add-zero", "probe.vector.reduction-add-zero"},
        {"vector-sub-zero", "probe.vector.sub-zero"},
        {"vector-or-zero", "probe.vector.or-zero"},
        {"vector-and-allones", "probe.vector.and-allones"},
        {"vector-smin", "probe.vector.smin"},
        {"vector-smax", "probe.vector.smax"},
        {"vector-umin", "probe.vector.umin"},
        {"vector-umax", "probe.vector.umax"},
        {"vector-abs", "probe.vector.abs"},
        {"vector-insert-extract-identity", "probe.vector.insert-extract-identity"},
        {"vector-reduction-add-single-lane", "probe.vector.reduction-add-single-lane"},
        {"vector-scalable-add-zero", "probe.vector.scalable.add-zero"},
        {"vector-scalable-mul-one", "probe.vector.scalable.mul-one"},
        {"vector-scalable-xor-self", "probe.vector.scalable.xor-self"},
        {"vector-scalable-sub-zero", "probe.vector.scalable.sub-zero"},
        {"vector-scalable-or-zero", "probe.vector.scalable.or-zero"},
        {"vector-scalable-and-allones", "probe.vector.scalable.and-allones"},
        {"vector-scalable-reduction-add-zero", "probe.vector.scalable.reduction-add-zero"},
        {"m-zero", "probe.instcombine.add-zero"},
        {"m-sub", "probe.instcombine.sub-zero"},
        {"m-one", "probe.instcombine.mul-one"},
        {"m-or", "probe.instcombine.or-zero"},
        {"m-allones", "probe.instcombine.and-allones"},
        {"m-and", "probe.instcombine.and-self"},
        {"xor-self", "probe.instcombine.xor-self"},
        {"dead-inst", "probe.dce.dead-instruction"},
        {"dead-global-init", "probe.globalopt.dead-initializer"},
        {"unreachable", "probe.simplifycfg.unreachable-block"},
        {"diamond", "probe.simplifycfg.diamond"},
        {"branch-chain", "probe.simplifycfg.branch-chain"},
        {"promotable-alloca", "probe.mem2reg.promotable-alloca"},
        {"store-load-forward", "probe.mem2reg.store-load-forward"},
        {"dead-store", "probe.dse.dead-store"},
        {"overwritten-store", "probe.dse.overwritten-store"},
        {"redundant-load", "probe.instcombine.redundant-load"},
        {"unused-alloca", "probe.cleanup.unused-alloca"},
        {"loop-header", "probe.loop.canonical-header"},
        {"induction-phi", "probe.loop.induction-phi"},
        {"simple-trip-count", "probe.loop.simple-trip-count"},
        {"invariant-op", "probe.licm.invariant-op"},
        {"dead-loop-inst", "probe.dce.dead-loop-instruction"},
        {"loop-exit", "probe.simplifycfg.loop-exit"},
    }};

} // namespace cv
