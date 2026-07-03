#pragma once

#include "o2t/GeneratorConfig.h"
#include "o2t/KleeCompat.h"

namespace cv {

#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
namespace detail {

inline std::uint8_t boundedByte(std::uint8_t value, std::uint8_t limit) {
  return static_cast<std::uint8_t>(value % limit);
}

inline std::int32_t smallConstant(std::int32_t value) {
  if (value >= -8 && value <= 8) {
    return value;
  }
  return static_cast<std::int32_t>((value % 17 + 17) % 17) - 8;
}

} // namespace detail

inline GeneratorConfig normalizeSymbolicConfig(GeneratorConfig config) {
  config.arithOpcode = detail::boundedByte(config.arithOpcode, 6);
  config.rhsMode = detail::boundedByte(config.rhsMode, 4);
  config.extraOpcode = detail::boundedByte(config.extraOpcode, 6);
  config.predicate = detail::boundedByte(config.predicate, 4);
  config.shape = detail::boundedByte(config.shape, 5);
  config.featureBits &= static_cast<std::uint8_t>(3);
  config.memoryShape = detail::boundedByte(config.memoryShape, 6);
  config.pointerMode = detail::boundedByte(config.pointerMode, 3);
  config.storeMode = detail::boundedByte(config.storeMode, 3);
  config.loadUseMode = detail::boundedByte(config.loadUseMode, 3);
  config.loopShape = detail::boundedByte(config.loopShape, 5);
  config.loopTripMode = detail::boundedByte(config.loopTripMode, 3);
  config.inductionMode = detail::boundedByte(config.inductionMode, 3);
  config.loopUseMode = detail::boundedByte(config.loopUseMode, 3);
  config.vectorShape = detail::boundedByte(config.vectorShape, 25);
  config.globalShape = detail::boundedByte(config.globalShape, 4);
  config.constA = detail::smallConstant(config.constA);
  config.constB = detail::smallConstant(config.constB);
  return config;
}

inline PatternCoverage symbolicCoverageFor(const GeneratorConfig &config) {
  PatternCoverage coverage;
  if (config.globalShape != 0) {
    coverage.hasGlobalDeadInitializer = true;
    return coverage;
  }
  if (config.vectorShape != 0) {
    coverage.hasVectorAddZero = config.vectorShape == 1;
    coverage.hasVectorMulOne = config.vectorShape == 2;
    coverage.hasVectorXorSelf = config.vectorShape == 3;
    coverage.hasVectorShuffleIdentity = config.vectorShape == 4;
    coverage.hasVectorShuffleSplat = config.vectorShape == 5;
    coverage.hasVectorExtractInsert = config.vectorShape == 6;
    coverage.hasVectorReductionAddZero = config.vectorShape == 7;
    coverage.hasVectorSubZero = config.vectorShape == 8;
    coverage.hasVectorOrZero = config.vectorShape == 9;
    coverage.hasVectorAndAllOnes = config.vectorShape == 10;
    coverage.hasVectorInsertExtractIdentity = config.vectorShape == 11;
    coverage.hasVectorReductionAddSingleLane = config.vectorShape == 12;
    coverage.hasVectorScalableAddZero = config.vectorShape == 13;
    coverage.hasVectorScalableMulOne = config.vectorShape == 14;
    coverage.hasVectorScalableXorSelf = config.vectorShape == 15;
    coverage.hasVectorScalableSubZero = config.vectorShape == 16;
    coverage.hasVectorScalableOrZero = config.vectorShape == 17;
    coverage.hasVectorScalableAndAllOnes = config.vectorShape == 18;
    coverage.hasVectorScalableReductionAddZero = config.vectorShape == 19;
    coverage.hasVectorSMin = config.vectorShape == 20;
    coverage.hasVectorSMax = config.vectorShape == 21;
    coverage.hasVectorUMin = config.vectorShape == 22;
    coverage.hasVectorUMax = config.vectorShape == 23;
    coverage.hasVectorAbs = config.vectorShape == 24;
    return coverage;
  }
  coverage.hasAddZero = (config.arithOpcode == 0 && config.rhsMode == 0) ||
                        config.extraOpcode == 1;
  coverage.hasSubZero = config.arithOpcode == 1 && config.rhsMode == 0;
  coverage.hasMulOne = (config.arithOpcode == 2 && config.rhsMode == 1) ||
                       config.extraOpcode == 2;
  coverage.hasXorSelf = config.extraOpcode == 3;
  coverage.hasOrZero = config.arithOpcode == 4 && config.rhsMode == 0;
  coverage.hasAndAllOnes =
      config.arithOpcode == 5 && config.rhsMode == 3 && config.constA == -1;
  coverage.hasAndSelf = config.extraOpcode == 5;
  coverage.hasDeadArithmetic = config.extraOpcode == 4;
  coverage.hasBranchDiamond = config.shape == 1;
  coverage.hasNestedDiamond = config.shape == 2;
  coverage.hasUnreachableTail = config.shape == 3;
  coverage.hasSwitchLikeChain = config.shape == 4;
  coverage.hasPromotableAlloca = config.memoryShape == 1 || config.memoryShape == 2;
  coverage.hasStoreLoadForward = config.memoryShape == 1 || config.memoryShape == 2;
  coverage.hasDeadStore = config.memoryShape == 3;
  coverage.hasOverwrittenStore = config.memoryShape == 4;
  coverage.hasRedundantLoad = config.memoryShape == 2;
  coverage.hasUnusedAlloca = config.memoryShape == 5;
  coverage.hasLoopCanonicalHeader = config.loopShape != 0;
  coverage.hasLoopInductionPhi = config.loopShape != 0;
  coverage.hasLoopSimpleTripCount = config.loopShape != 0;
  coverage.hasLoopInvariantOp = config.loopShape == 3;
  coverage.hasDeadLoopInstruction = config.loopShape == 4;
  coverage.hasLoopExit = config.loopShape != 0;
  return coverage;
}
#endif

inline GeneratorConfig makeSymbolicConfig() {
#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
  GeneratorConfig config{};
#else
  GeneratorConfig config = defaultConfig();
#endif

  klee_make_symbolic(&config.arithOpcode, sizeof(config.arithOpcode),
                     "arith_opcode");
  klee_make_symbolic(&config.rhsMode, sizeof(config.rhsMode), "rhs_mode");
  klee_make_symbolic(&config.extraOpcode, sizeof(config.extraOpcode),
                     "extra_opcode");
  klee_make_symbolic(&config.predicate, sizeof(config.predicate), "predicate");
  klee_make_symbolic(&config.shape, sizeof(config.shape), "shape");
  klee_make_symbolic(&config.featureBits, sizeof(config.featureBits),
                     "feature_bits");
  klee_make_symbolic(&config.memoryShape, sizeof(config.memoryShape),
                     "memory_shape");
  klee_make_symbolic(&config.pointerMode, sizeof(config.pointerMode),
                     "pointer_mode");
  klee_make_symbolic(&config.storeMode, sizeof(config.storeMode), "store_mode");
  klee_make_symbolic(&config.loadUseMode, sizeof(config.loadUseMode),
                     "load_use_mode");
  klee_make_symbolic(&config.loopShape, sizeof(config.loopShape), "loop_shape");
  klee_make_symbolic(&config.loopTripMode, sizeof(config.loopTripMode),
                     "loop_trip_mode");
  klee_make_symbolic(&config.inductionMode, sizeof(config.inductionMode),
                     "induction_mode");
  klee_make_symbolic(&config.loopUseMode, sizeof(config.loopUseMode),
                     "loop_use_mode");
  klee_make_symbolic(&config.vectorShape, sizeof(config.vectorShape),
                     "vector_shape");
  klee_make_symbolic(&config.globalShape, sizeof(config.globalShape),
                     "global_shape");
  klee_make_symbolic(&config.constA, sizeof(config.constA), "const_a");
  klee_make_symbolic(&config.constB, sizeof(config.constB), "const_b");

#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
  return normalizeSymbolicConfig(config);
#else
  return normalizeConfig(config);
#endif
}

} // namespace cv
