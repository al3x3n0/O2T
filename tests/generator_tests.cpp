#include "o2t/GeneratorConfig.h"
#include "o2t/ConfigReducer.h"
#include "o2t/IRTextGenerator.h"
#include "o2t/PassProbes.h"
#include "o2t/PassInstrumentation.h"
#include "o2t/ProbeMarkers.h"
#include "o2t/ProbeOracle.h"
#include "o2t/ProbeBackend.h"
#include "o2t/SymbolicConfig.h"
#include "o2t/GeneratedKleeFeedback.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>

namespace {

void testDefaultGeneratesAddZero() {
  const cv::GeneratedIR generated = cv::generateIR(cv::defaultConfig());
  assert(generated.moduleText.find("define i32 @test") != std::string::npos);
  assert(generated.moduleText.find("%x = add nsw i32 %a, 0") != std::string::npos);
  assert(generated.coverage.hasAddZero);
}

void testDiamondGeneratesPhi() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.predicate = static_cast<unsigned char>(cv::Predicate::Slt);
  config.featureBits = 0;

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("br i1 %cmp") != std::string::npos);
  assert(generated.moduleText.find("phi i32") != std::string::npos);
  assert(generated.coverage.hasBranchDiamond);
}

void testSelectVariant() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.featureBits = 2;

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("select i1 %cmp") != std::string::npos);
  // The select form must be branchless so its operands dominate the select;
  // a branching diamond feeding a select in the merge block is invalid IR.
  assert(generated.moduleText.find("br i1 %cmp") == std::string::npos);
  assert(generated.moduleText.find("%then.v = ") <
         generated.moduleText.find("select i1 %cmp"));
}

void testConfigParseNormalizes() {
  std::istringstream input("arith_opcode=99\nrhs_mode=99\nextra_opcode=99\n"
                           "predicate=99\nshape=99\nfeature_bits=255\n"
                           "memory_shape=99\npointer_mode=99\n"
                           "store_mode=99\nload_use_mode=99\n"
                           "loop_shape=99\nloop_trip_mode=99\n"
                           "induction_mode=99\nloop_use_mode=99\n"
                           "vector_shape=99\n"
                           "global_shape=99\n"
                           "compose_bits=99\n"
                           "int_width=99\n"
                           "scalar_args=99\n"
                           "pointer_args=99\n"
                           "pointer_noalias=99\n"
                           "cast_mode=99\n"
                           "const_a=123\nconst_b=-123\n");
  cv::GeneratorConfig config{};
  std::string error;
  assert(cv::parseConfig(input, config, error));
  assert(config.arithOpcode < 6);
  assert(config.rhsMode < 4);
  assert(config.extraOpcode < 6);
  assert(config.predicate < 4);
  assert(config.shape < 5);
  assert(config.featureBits < 8);
  assert(config.memoryShape < 6);
  assert(config.pointerMode < 3);
  assert(config.storeMode < 3);
  assert(config.loadUseMode < 3);
  assert(config.loopShape < 5);
  assert(config.loopTripMode < 3);
  assert(config.inductionMode < 3);
  assert(config.loopUseMode < 3);
  assert(config.vectorShape < 25);
  assert(config.globalShape < 4);
  assert(config.composeBits < 64);
  assert(config.intWidth < 4);
  assert(config.scalarArgs < 3);
  assert(config.pointerArgs < 3);
  assert(config.pointerNoalias < 2);
  assert(config.castMode < 8);
  assert(config.constA >= -8 && config.constA <= 8);
  assert(config.constB >= -8 && config.constB <= 8);
}

void testSmallConfigConstantsAreStable() {
  std::istringstream input("const_a=0\nconst_b=1\n");
  cv::GeneratorConfig config{};
  std::string error;
  assert(cv::parseConfig(input, config, error));
  assert(config.constA == 0);
  assert(config.constB == 1);
}

void testNativeSymbolicConfigUsesDefaultConfig() {
  const cv::GeneratorConfig config = cv::makeSymbolicConfig();
  assert(config.arithOpcode == static_cast<unsigned char>(cv::ArithOpcode::Add));
  assert(config.rhsMode == static_cast<unsigned char>(cv::RhsMode::Zero));
  assert(config.constA == 0);
  assert(config.constB == 1);
}

void testNestedDiamondShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::NestedDiamond);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("then.outer:") != std::string::npos);
  assert(generated.moduleText.find("then.inner:") != std::string::npos);
  assert(generated.moduleText.find("[ %else.outer.v, %else.outer ]") !=
         std::string::npos);
  assert(generated.coverage.hasNestedDiamond);
}

void testUnreachableTailShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::UnreachableTail);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("unreachable.tail:") != std::string::npos);
  assert(generated.moduleText.find("unreachable\n") != std::string::npos);
  assert(generated.coverage.hasUnreachableTail);
}

void testSwitchLikeChainShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::SwitchLikeChain);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("check.b:") != std::string::npos);
  assert(generated.moduleText.find("case.a:") != std::string::npos);
  assert(generated.moduleText.find("[ %default.v, %default ]") !=
         std::string::npos);
  assert(generated.coverage.hasSwitchLikeChain);
}

void testAllocaStoreLoadShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::AllocaStoreLoad);
  config.pointerMode = static_cast<unsigned char>(cv::PointerMode::DirectSlot);
  config.storeMode = static_cast<unsigned char>(cv::StoreMode::SingleStore);
  config.loadUseMode = static_cast<unsigned char>(cv::LoadUseMode::ReturnedLoad);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%slot = alloca i32") != std::string::npos);
  assert(generated.moduleText.find("store i32 %a, ptr %slot") != std::string::npos);
  assert(generated.moduleText.find("%loaded = load i32, ptr %slot") != std::string::npos);
  assert(generated.coverage.hasPromotableAlloca);
  assert(generated.coverage.hasStoreLoadForward);
}

void testConditionalStoreMemoryShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::AllocaStoreLoad);
  config.storeMode = static_cast<unsigned char>(cv::StoreMode::ConditionalStore);
  config.loadUseMode = static_cast<unsigned char>(cv::LoadUseMode::ArithmeticUse);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("store.then:") != std::string::npos);
  assert(generated.moduleText.find("store.merge:") != std::string::npos);
  assert(generated.moduleText.find("%use = add i32 %loaded, %b") != std::string::npos);
}

void testIndexedSlotMemoryShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::DeadStore);
  config.pointerMode = static_cast<unsigned char>(cv::PointerMode::IndexedSlot);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("alloca [2 x i32]") != std::string::npos);
  assert(generated.moduleText.find("getelementptr inbounds [2 x i32]") !=
         std::string::npos);
  assert(generated.coverage.hasDeadStore);
}

void testLoadAfterStoreShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::LoadAfterStore);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%loaded.again = load i32") != std::string::npos);
  assert(generated.coverage.hasRedundantLoad);
}

void testOverwrittenStoreShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::OverwrittenStore);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("store i32 0, ptr %slot") != std::string::npos);
  assert(generated.moduleText.find("store i32 %a, ptr %slot") != std::string::npos);
  assert(generated.coverage.hasOverwrittenStore);
}

void testUnusedAllocaShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::UnusedAlloca);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%slot = alloca i32") != std::string::npos);
  assert(generated.moduleText.find("store i32") == std::string::npos);
  assert(generated.coverage.hasUnusedAlloca);
}

void testCountedLoopShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  config.loopTripMode = static_cast<unsigned char>(cv::LoopTripMode::ConstantSmall);
  config.inductionMode = static_cast<unsigned char>(cv::InductionMode::IncrementByOne);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("loop.header:") != std::string::npos);
  assert(generated.moduleText.find("%i = phi i32") != std::string::npos);
  assert(generated.moduleText.find("br i1 %loop.cond") != std::string::npos);
  assert(generated.coverage.hasLoopCanonicalHeader);
  assert(generated.coverage.hasLoopInductionPhi);
  assert(generated.coverage.hasLoopSimpleTripCount);
}

void testArgumentBoundedLoopShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  config.loopTripMode = static_cast<unsigned char>(cv::LoopTripMode::ArgumentBounded);
  config.inductionMode = static_cast<unsigned char>(cv::InductionMode::Decrement);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%limit.mask = and i32 %b, 7") !=
         std::string::npos);
  assert(generated.moduleText.find("icmp sgt i32 %i, 0") != std::string::npos);
  assert(generated.moduleText.find("%next = add i32 %i, -1") !=
         std::string::npos);
}

void testEarlyExitLoopShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::EarlyExitLoop);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%early.exit = icmp eq i32 %body.v") !=
         std::string::npos);
  assert(generated.coverage.hasLoopExit);
}

void testInvariantLoopShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::InvariantOpLoop);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%invariant = add i32 %a") !=
         std::string::npos);
  assert(generated.coverage.hasLoopInvariantOp);
}

void testDeadBodyLoopShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::DeadBodyLoop);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%loop.dead = add i32 %a") !=
         std::string::npos);
  assert(generated.coverage.hasDeadLoopInstruction);
}

void testPassProbeFindsInstCombinePatterns() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.arithOpcode = static_cast<unsigned char>(cv::ArithOpcode::Mul);
  config.rhsMode = static_cast<unsigned char>(cv::RhsMode::One);
  config.extraOpcode = static_cast<unsigned char>(cv::ExtraOpcode::XorSelf);

  const cv::AbstractFunction function = cv::buildAbstractFunction(config);
  const cv::PassProbeCoverage coverage = cv::scanOptimizationProbes(function);

  assert(coverage.instcombineMulOne);
  assert(coverage.instcombineXorSelf);
}

void testPassProbeFindsCfgPatterns() {
  cv::GeneratorConfig nested = cv::defaultConfig();
  nested.shape = static_cast<unsigned char>(cv::Shape::NestedDiamond);
  const cv::PassProbeCoverage nestedCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(nested));
  assert(nestedCoverage.simplifycfgNestedBranch);

  cv::GeneratorConfig unreachable = cv::defaultConfig();
  unreachable.shape = static_cast<unsigned char>(cv::Shape::UnreachableTail);
  const cv::PassProbeCoverage unreachableCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(unreachable));
  assert(unreachableCoverage.simplifycfgUnreachableBlock);

  cv::GeneratorConfig chain = cv::defaultConfig();
  chain.shape = static_cast<unsigned char>(cv::Shape::SwitchLikeChain);
  const cv::PassProbeCoverage chainCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(chain));
  assert(chainCoverage.simplifycfgBranchChain);
}

void testPassProbeFindsMemoryPatterns() {
  cv::GeneratorConfig promotable = cv::defaultConfig();
  promotable.memoryShape =
      static_cast<unsigned char>(cv::MemoryShape::AllocaStoreLoad);
  const cv::PassProbeCoverage promotableCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(promotable));
  assert(promotableCoverage.mem2regPromotableAlloca);
  assert(promotableCoverage.mem2regStoreLoadForward);

  cv::GeneratorConfig redundant = cv::defaultConfig();
  redundant.memoryShape =
      static_cast<unsigned char>(cv::MemoryShape::LoadAfterStore);
  const cv::PassProbeCoverage redundantCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(redundant));
  assert(redundantCoverage.instcombineRedundantLoad);

  cv::GeneratorConfig deadStore = cv::defaultConfig();
  deadStore.memoryShape = static_cast<unsigned char>(cv::MemoryShape::DeadStore);
  const cv::PassProbeCoverage deadCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(deadStore));
  assert(deadCoverage.dseDeadStore);

  cv::GeneratorConfig unused = cv::defaultConfig();
  unused.memoryShape = static_cast<unsigned char>(cv::MemoryShape::UnusedAlloca);
  const cv::PassProbeCoverage unusedCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(unused));
  assert(unusedCoverage.cleanupUnusedAlloca);
}

void testPassProbeFindsLoopPatterns() {
  cv::GeneratorConfig counted = cv::defaultConfig();
  counted.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  const cv::PassProbeCoverage countedCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(counted));
  assert(countedCoverage.loopCanonicalHeader);
  assert(countedCoverage.loopInductionPhi);
  assert(countedCoverage.loopSimpleTripCount);
  assert(countedCoverage.simplifycfgLoopExit);

  cv::GeneratorConfig invariant = cv::defaultConfig();
  invariant.loopShape = static_cast<unsigned char>(cv::LoopShape::InvariantOpLoop);
  const cv::PassProbeCoverage invariantCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(invariant));
  assert(invariantCoverage.licmInvariantOp);

  cv::GeneratorConfig dead = cv::defaultConfig();
  dead.loopShape = static_cast<unsigned char>(cv::LoopShape::DeadBodyLoop);
  const cv::PassProbeCoverage deadCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(dead));
  assert(deadCoverage.dceDeadLoopInstruction);
}

void testVectorShapeGeneratesVectorIR() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::ScalableOrZero);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("<vscale x 4 x i32>") != std::string::npos);
  assert(generated.moduleText.find("or <vscale x 4 x i32>") != std::string::npos);
  assert(generated.coverage.hasVectorScalableOrZero);
}

void testVectorMinMaxAbsShapesGenerateVectorIR() {
  struct Case {
    cv::VectorShape shape;
    const char *needle;
    bool cv::PatternCoverage::*coverage;
  };

  const Case cases[] = {
      {cv::VectorShape::SMin, "icmp slt <4 x i32>", &cv::PatternCoverage::hasVectorSMin},
      {cv::VectorShape::SMax, "icmp sgt <4 x i32>", &cv::PatternCoverage::hasVectorSMax},
      {cv::VectorShape::UMin, "icmp ult <4 x i32>", &cv::PatternCoverage::hasVectorUMin},
      {cv::VectorShape::UMax, "icmp ugt <4 x i32>", &cv::PatternCoverage::hasVectorUMax},
      {cv::VectorShape::Abs, "sub <4 x i32> zeroinitializer, %vec", &cv::PatternCoverage::hasVectorAbs},
  };

  for (const Case &testCase : cases) {
    cv::GeneratorConfig config = cv::defaultConfig();
    config.vectorShape = static_cast<unsigned char>(testCase.shape);

    const cv::GeneratedIR generated = cv::generateIR(config);
    assert(generated.moduleText.find(testCase.needle) != std::string::npos);
    assert(generated.moduleText.find("select <4 x i1>") != std::string::npos);
    assert(generated.coverage.*testCase.coverage);
  }
}

void testGlobalShapeGeneratesI32Witness() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerI32);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("define i32 @test") == std::string::npos);
  assert(generated.moduleText.find("; marker=probe.globalopt.dead-initializer") !=
         std::string::npos);
  assert(generated.moduleText.find("@cv_dead_init = internal global i32 42") !=
         std::string::npos);
  assert(generated.moduleText.find("define i32 @cv_observe") !=
         std::string::npos);
  assert(generated.coverage.hasGlobalDeadInitializer);
}

void testGlobalShapeGeneratesPtrWitness() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerPtr);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("@cv_target = internal global i32 7") !=
         std::string::npos);
  assert(generated.moduleText.find("@cv_dead_init = internal global ptr @cv_target") !=
         std::string::npos);
  assert(generated.coverage.hasGlobalDeadInitializer);
}

void testGlobalShapeGeneratesArrayWitness() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerArray);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find(
             "@cv_dead_init = internal global [2 x i32] [i32 1, i32 2]") !=
         std::string::npos);
  assert(generated.coverage.hasGlobalDeadInitializer);
}

void testPassProbeFindsVectorPatterns() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.vectorShape =
      static_cast<unsigned char>(cv::VectorShape::ReductionAddSingleLane);

  const cv::PassProbeCoverage coverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(config));
  assert(coverage.vectorReductionAddSingleLane);

  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::SMin);
  const cv::PassProbeCoverage sminCoverage =
      cv::scanOptimizationProbes(cv::buildAbstractFunction(config));
  assert(sminCoverage.vectorSMin);
}

void testReducerPreservesSelectedMarkers() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.arithOpcode = static_cast<unsigned char>(cv::ArithOpcode::Mul);
  config.rhsMode = static_cast<unsigned char>(cv::RhsMode::One);
  config.extraOpcode = static_cast<unsigned char>(cv::ExtraOpcode::MulOne);
  config.shape = static_cast<unsigned char>(cv::Shape::SwitchLikeChain);
  config.featureBits = 3;
  config.constA = 7;
  config.constB = -4;

  const std::vector<std::string> required = {
      "probe.instcombine.mul-one",
      "probe.simplifycfg.branch-chain",
  };

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, required);
  assert(cv::preservesMarkers(reduced, required));
  assert(reduced.constA == 0);
  assert(reduced.constB == 0);
  assert(reduced.shape == static_cast<unsigned char>(cv::Shape::SwitchLikeChain));
}

void testReducerKeepsUnreachableShape() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::UnreachableTail);
  config.constA = 8;
  config.constB = -8;

  const std::vector<std::string> required = {
      "probe.simplifycfg.unreachable-block",
  };

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, required);
  assert(cv::preservesMarkers(reduced, required));
  assert(reduced.shape == static_cast<unsigned char>(cv::Shape::UnreachableTail));
}

void testReducerWithNoMarkersMinimizesToDefaults() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.arithOpcode = static_cast<unsigned char>(cv::ArithOpcode::Sub);
  config.rhsMode = static_cast<unsigned char>(cv::RhsMode::ArgumentB);
  config.extraOpcode = static_cast<unsigned char>(cv::ExtraOpcode::None);
  config.shape = static_cast<unsigned char>(cv::Shape::StraightLine);
  config.featureBits = 3;
  config.constA = 5;
  config.constB = -5;

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, {});
  assert(reduced.featureBits == 0);
  assert(reduced.constA == 0);
  assert(reduced.constB == 0);
  assert(reduced.predicate == 0);
  assert(reduced.extraOpcode == 0);
  assert(reduced.rhsMode == 0);
  assert(reduced.arithOpcode == 0);
  assert(reduced.shape == 0);
}

void testReducerPreservesMemoryMarkers() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::LoadAfterStore);
  config.pointerMode = static_cast<unsigned char>(cv::PointerMode::IndexedSlot);
  config.storeMode = static_cast<unsigned char>(cv::StoreMode::ConditionalStore);
  config.loadUseMode = static_cast<unsigned char>(cv::LoadUseMode::ArithmeticUse);
  config.constA = 7;
  config.constB = -7;

  const std::vector<std::string> required = {
      "probe.instcombine.redundant-load",
  };

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, required);
  assert(cv::preservesMarkers(reduced, required));
  assert(reduced.memoryShape ==
         static_cast<unsigned char>(cv::MemoryShape::LoadAfterStore));
  assert(reduced.pointerMode == 0);
  assert(reduced.storeMode == 0);
  assert(reduced.loadUseMode == 0);
}

void testReducerPreservesLoopMarkers() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::InvariantOpLoop);
  config.loopTripMode = static_cast<unsigned char>(cv::LoopTripMode::ArgumentBounded);
  config.inductionMode =
      static_cast<unsigned char>(cv::InductionMode::IncrementByConstant);
  config.loopUseMode = static_cast<unsigned char>(cv::LoopUseMode::ReturnAccumulator);
  config.constA = 6;
  config.constB = -6;

  const std::vector<std::string> required = {
      "probe.licm.invariant-op",
  };

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, required);
  assert(cv::preservesMarkers(reduced, required));
  assert(reduced.loopShape ==
         static_cast<unsigned char>(cv::LoopShape::InvariantOpLoop));
  assert(reduced.loopTripMode == 0);
  assert(reduced.inductionMode == 0);
  assert(reduced.loopUseMode == 0);
}

void testReducerPreservesVectorMarkers() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::SMax);
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::OverwrittenStore);
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::InvariantOpLoop);
  config.constA = 7;
  config.constB = -7;

  const std::vector<std::string> required = {
      "probe.vector.smax",
  };

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, required);
  assert(cv::preservesMarkers(reduced, required));
  assert(reduced.vectorShape ==
         static_cast<unsigned char>(cv::VectorShape::SMax));
  assert(reduced.memoryShape == 0);
  assert(reduced.loopShape == 0);
}

void testReducerPreservesGlobalMarkers() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerArray);
  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::ShuffleIdentity);
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::OverwrittenStore);
  config.constA = 7;
  config.constB = -7;

  const std::vector<std::string> required = {
      "probe.globalopt.dead-initializer",
  };

  const cv::GeneratorConfig reduced = cv::reduceConfig(config, required);
  assert(cv::preservesMarkers(reduced, required));
  assert(reduced.globalShape ==
         static_cast<unsigned char>(cv::GlobalShape::DeadInitializerI32));
  assert(reduced.vectorShape == 0);
  assert(reduced.memoryShape == 0);
}

void testPassInstrumentationPreservesPredicateValue() {
  cv::clearPassProbeEvents();
  assert(CV_PASS_PROBE_IF("probe.test.true", true));
  assert(!CV_PASS_PROBE_IF("probe.test.false", false));
  const std::vector<std::string> firstEvents = cv::passProbeEvents();
  assert(firstEvents.size() == 1);
  assert(firstEvents[0] == "probe.test.true");

  CV_PASS_PROBE("probe.test.unconditional");
  const std::vector<std::string> secondEvents = cv::passProbeEvents();
  assert(secondEvents.size() == 2);
  assert(secondEvents[1] == "probe.test.unconditional");

  cv::clearPassProbeEvents();
  assert(cv::passProbeEvents().empty());
}

void testPassInstrumentationWritesNativeProbeLog() {
  const char *path = "pass-probe-events-test.log";
  std::remove(path);
  unsetenv("COMPILERVERIF_PASS_PROBE_LOG");
  setenv("O2T_PASS_PROBE_LOG", path, 1);

  cv::clearPassProbeEvents();
  CV_PASS_PROBE_IF("probe.test.logged", true);
  CV_PASS_PROBE_IF("probe.test.not-logged", false);
  unsetenv("O2T_PASS_PROBE_LOG");

  std::ifstream input(path);
  std::string line;
  assert(std::getline(input, line));
  assert(line == "probe.test.logged");
  assert(!std::getline(input, line));
  std::remove(path);
}

void testPassInstrumentationWritesLegacyProbeLog() {
  const char *path = "pass-probe-events-legacy-test.log";
  std::remove(path);
  unsetenv("O2T_PASS_PROBE_LOG");
  setenv("COMPILERVERIF_PASS_PROBE_LOG", path, 1);

  cv::clearPassProbeEvents();
  CV_PASS_PROBE_IF("probe.test.legacy-logged", true);
  CV_PASS_PROBE_IF("probe.test.legacy-not-logged", false);
  unsetenv("COMPILERVERIF_PASS_PROBE_LOG");

  std::ifstream input(path);
  std::string line;
  assert(std::getline(input, line));
  assert(line == "probe.test.legacy-logged");
  assert(!std::getline(input, line));
  std::remove(path);
}

void testProbeOracleExactMatch() {
  const cv::ProbeOracleResult result = cv::evaluateProbeOracle(
      {"probe.a", "probe.b"}, {"probe.a", "probe.b"});
  assert(result.status == cv::ProbeOracleStatus::Matched);
  assert(result.missingMarkers.empty());
  assert(result.unexpectedMarkers.empty());
  assert(cv::containsAllMarkers(result.observedMarkers, result.expectedMarkers));
}

void testProbeOracleMissingMarker() {
  const cv::ProbeOracleResult result =
      cv::evaluateProbeOracle({"probe.a", "probe.b"}, {"probe.a"});
  assert(result.status == cv::ProbeOracleStatus::Mismatch);
  assert(result.missingMarkers.size() == 1);
  assert(result.missingMarkers[0] == "probe.b");
  assert(result.unexpectedMarkers.empty());
}

void testProbeOracleUnexpectedMarker() {
  const cv::ProbeOracleResult result =
      cv::evaluateProbeOracle({"probe.a"}, {"probe.a", "probe.extra"});
  assert(result.status == cv::ProbeOracleStatus::Mismatch);
  assert(result.missingMarkers.empty());
  assert(result.unexpectedMarkers.size() == 1);
  assert(result.unexpectedMarkers[0] == "probe.extra");
}

void testProbeOracleAllowsExtraObserved() {
  const cv::ProbeOracleResult result =
      cv::evaluateProbeOracle({"probe.a"}, {"probe.a", "probe.extra"}, true);
  assert(result.status == cv::ProbeOracleStatus::Matched);
  assert(result.missingMarkers.empty());
  assert(result.unexpectedMarkers.empty());
}

void testProbeOracleEmptyObservedIsNotInstrumented() {
  const cv::ProbeOracleResult result =
      cv::evaluateProbeOracle({"probe.a"}, {});
  assert(result.status == cv::ProbeOracleStatus::NotInstrumented);
  assert(result.missingMarkers.size() == 1);
  assert(result.missingMarkers[0] == "probe.a");
}

void testProbeBackendBoundary() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);

  const cv::ProbeBackendResult abstractResult =
      cv::runAbstractProbeBackend(config);
  assert(abstractResult.kind == cv::ProbeBackendKind::Abstract);
  assert(abstractResult.available);
  assert(abstractResult.coverage.simplifycfgDiamond);

  const cv::ProbeBackendResult llvmResult = cv::runLLVMProbeBackend(config);
  assert(llvmResult.kind == cv::ProbeBackendKind::LLVM);
  assert(!llvmResult.available);
}

void testComposeBitsZeroKeepsLegacyOutput() {
  // With composeBits == 0 the generator must emit byte-identical legacy IR.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.featureBits = 0;
  config.composeBits = 0;

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%merged = phi i32") != std::string::npos);
  assert(generated.moduleText.find("%s0.") == std::string::npos);
}

void testComposeCfgMemoryLoopThreadsValue() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::AllocaStoreLoad);
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  config.extraOpcode = static_cast<unsigned char>(cv::ExtraOpcode::AddZero);
  config.composeBits = static_cast<unsigned char>(
      cv::ComposeCfg | cv::ComposeMemory | cv::ComposeLoop);

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;

  // All three regions co-exist, prefixed per stage.
  assert(ir.find("s0.merge:") != std::string::npos);          // CFG stage
  assert(ir.find("%s1.slot = alloca") != std::string::npos);  // memory stage
  assert(ir.find("s2.loop.header:") != std::string::npos);    // loop stage

  // Value threading: CFG result feeds the store, loaded value seeds the loop acc.
  assert(ir.find("store i32 %s0.merged, ptr %s1.slot") != std::string::npos);
  assert(ir.find("%s2.acc = phi i32 [ %s1.loaded, %s0.merge ]") !=
         std::string::npos);

  // Exactly one terminating ret, fed by the trailing extra fold.
  assert(ir.find("%fin.extra = add") != std::string::npos);
  assert(ir.find("ret i32 %fin.extra") != std::string::npos);
}

void testComposeSelectsOnlyEnabledDimensions() {
  // Only the memory bit is set; CFG/loop regions must be absent.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::DeadStore);
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  config.composeBits = static_cast<unsigned char>(cv::ComposeMemory);

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("%s0.slot = alloca") != std::string::npos);
  assert(ir.find(".merge:") == std::string::npos);
  assert(ir.find(".loop.header:") == std::string::npos);
}

void testComposeBitsRoundTripsThroughConfig() {
  std::istringstream input("shape=1\nmemory_shape=1\ncompose_bits=7\n");
  cv::GeneratorConfig config{};
  std::string error;
  assert(cv::parseConfig(input, config, error));
  assert(config.composeBits == 7);

  std::ostringstream out;
  cv::writeConfig(out, config);
  assert(out.str().find("compose_bits=7") != std::string::npos);
}

void testComposeVectorAndGlobalIgnoredWithoutBits() {
  // Vector/global shapes stay legacy unless their compose bit is set. With only
  // CFG/memory/loop bits, the vector shape is not emitted and there is no merge
  // -- just the straight-line CFG stage.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::AddZero);
  config.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerI32);
  config.composeBits = static_cast<unsigned char>(cv::ComposeCfg);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("<4 x i32>") == std::string::npos);
  assert(generated.moduleText.find("@cv_dead_init") == std::string::npos);
  assert(generated.moduleText.find("%s0.x") != std::string::npos);
}

void testComposeVectorThreadsScalar() {
  // The vector bit lifts the running scalar into a vector and extracts it back,
  // so a CFG result feeds the vector lanes within one function.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::AddZero);
  config.composeBits = static_cast<unsigned char>(cv::ComposeCfg | cv::ComposeVector);

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("s0.merge:") != std::string::npos);
  assert(ir.find("%s1.v0 = insertelement <4 x i32> poison, i32 %s0.merged") !=
         std::string::npos);
  assert(ir.find("%s1.result = extractelement <4 x i32>") != std::string::npos);
  assert(generated.coverage.hasBranchDiamond);
  assert(generated.coverage.hasVectorAddZero);
}

void testComposeVectorReductionEmitsDeclare() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.vectorShape =
      static_cast<unsigned char>(cv::VectorShape::ReductionAddZero);
  config.composeBits = static_cast<unsigned char>(cv::ComposeVector);

  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find(
             "declare i32 @llvm.vector.reduce.add.v4i32(<4 x i32>)") !=
         std::string::npos);
  assert(generated.moduleText.find("call i32 @llvm.vector.reduce.add.v4i32") !=
         std::string::npos);
}

void testComposeGlobalAtModuleScope() {
  // A composed global is emitted at module scope alongside the composed @test,
  // preserving its dead-initializer witness while the function carries a loop.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  config.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerI32);
  config.composeBits = static_cast<unsigned char>(cv::ComposeLoop | cv::ComposeGlobal);

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("@cv_dead_init = internal global i32 42") != std::string::npos);
  assert(ir.find("define i32 @test(i32 %a, i32 %b)") != std::string::npos);
  assert(ir.find("s0.loop.header:") != std::string::npos);
  assert(generated.coverage.hasGlobalDeadInitializer);
  assert(generated.coverage.hasLoopCanonicalHeader);

  // composeBits == 0 keeps the legacy global-only module (no @test, no s0.).
  cv::GeneratorConfig legacy = cv::defaultConfig();
  legacy.globalShape =
      static_cast<unsigned char>(cv::GlobalShape::DeadInitializerI32);
  const cv::GeneratedIR legacyIR = cv::generateIR(legacy);
  assert(legacyIR.moduleText.find("@cv_dead_init") != std::string::npos);
  assert(legacyIR.moduleText.find("@test") == std::string::npos);
}

void testComposeIntWidthRetypesFunction() {
  // A non-default int width retypes the whole composed function consistently;
  // vector lane indices stay i32 while element types follow the width.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.predicate = static_cast<unsigned char>(cv::Predicate::Slt);
  config.loopShape = static_cast<unsigned char>(cv::LoopShape::CountedLoop);
  config.vectorShape = static_cast<unsigned char>(cv::VectorShape::AddZero);
  config.composeBits = static_cast<unsigned char>(
      cv::ComposeCfg | cv::ComposeLoop | cv::ComposeVector);
  config.intWidth = static_cast<unsigned char>(cv::IntWidth::I64);

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("define i64 @test(i64 %a, i64 %b)") != std::string::npos);
  assert(ir.find("%s0.cmp = icmp slt i64 %a, %b") != std::string::npos);
  assert(ir.find("phi i64") != std::string::npos);
  assert(ir.find("alloca i64") == std::string::npos);  // no memory bit set
  assert(ir.find("insertelement <4 x i64>") != std::string::npos);
  assert(ir.find(", i32 0\n") != std::string::npos);  // lane index stays i32
  assert(ir.find("i32 %a") == std::string::npos);     // nothing left at i32
}

void testComposeIntWidthDefaultIsI32() {
  // Default width keeps composed output at i32 (byte-compatible with prior runs).
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::Diamond);
  config.composeBits = static_cast<unsigned char>(cv::ComposeCfg);
  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("define i32 @test(i32 %a, i32 %b)") !=
         std::string::npos);
}

void testComposeExtraScalarArgs() {
  // Four scalar args extend the signature; %c and %d are folded into the
  // threaded value so they are live.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.shape = static_cast<unsigned char>(cv::Shape::StraightLine);
  config.composeBits = static_cast<unsigned char>(cv::ComposeCfg);
  config.scalarArgs = 2;  // -> 4 args

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("@test(i32 %a, i32 %b, i32 %c, i32 %d)") != std::string::npos);
  assert(ir.find("%argfold2 = add i32 %a, %c") != std::string::npos);
  assert(ir.find("%argfold3 = add i32 %argfold2, %d") != std::string::npos);

  // Default (2 args) keeps the legacy signature and no fold.
  cv::GeneratorConfig two = cv::defaultConfig();
  two.composeBits = static_cast<unsigned char>(cv::ComposeCfg);
  const cv::GeneratedIR twoIR = cv::generateIR(two);
  assert(twoIR.moduleText.find("@test(i32 %a, i32 %b)") != std::string::npos);
  assert(twoIR.moduleText.find("argfold") == std::string::npos);
}

void testComposePointerParamsAliasPattern() {
  // Two noalias pointer params route the memory stage through %p/%q with an
  // interposed store to %q -- the alias-sensitive redundant-load pattern.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.memoryShape = static_cast<unsigned char>(cv::MemoryShape::LoadAfterStore);
  config.composeBits = static_cast<unsigned char>(cv::ComposeMemory);
  config.pointerArgs = 2;
  config.pointerNoalias = 1;

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("ptr noalias %p, ptr noalias %q") != std::string::npos);
  assert(ir.find("store i32 %a, ptr %p") != std::string::npos);
  assert(ir.find(", ptr %q, align 4") != std::string::npos);  // interferer store
  assert(ir.find("load i32, ptr %p") != std::string::npos);
  assert(ir.find("alloca") == std::string::npos);  // routed through params

  // Without the noalias flag the params are plain pointers.
  cv::GeneratorConfig mayAlias = config;
  mayAlias.pointerNoalias = 0;
  const cv::GeneratedIR mayAliasIR = cv::generateIR(mayAlias);
  assert(mayAliasIR.moduleText.find("ptr %p, ptr %q") != std::string::npos);
  assert(mayAliasIR.moduleText.find("ptr noalias") == std::string::npos);
}

void testComposeCastStageRoundTrips() {
  // Cast stage narrows i32 -> i8 and widens back, threading the value at i32.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.composeBits = static_cast<unsigned char>(cv::ComposeCfg | cv::ComposeCast);
  config.intWidth = static_cast<unsigned char>(cv::IntWidth::I32);
  config.castMode = 4;  // target width index 0 (i8), signed

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("trunc i32 ") != std::string::npos);
  assert(ir.find(" to i8\n") != std::string::npos);
  assert(ir.find("sext i8 ") != std::string::npos);
  assert(ir.find(" to i32\n") != std::string::npos);
  assert(ir.find("ret i32 %s1.result") != std::string::npos);
}

void testComposeCastStageWidens() {
  // From i8 the cast stage must widen first (no narrower type), then trunc back.
  cv::GeneratorConfig config = cv::defaultConfig();
  config.composeBits = static_cast<unsigned char>(cv::ComposeCast);
  config.intWidth = static_cast<unsigned char>(cv::IntWidth::I8);
  config.castMode = 3;  // target width index 3 (i64), unsigned

  const cv::GeneratedIR generated = cv::generateIR(config);
  const std::string &ir = generated.moduleText;
  assert(ir.find("zext i8 ") != std::string::npos);
  assert(ir.find(" to i64\n") != std::string::npos);
  assert(ir.find("trunc i64 ") != std::string::npos);
  assert(ir.find(" to i8\n") != std::string::npos);
}

void testKleeFeedbackNoveltyPredicate() {
  // The committed feedback header has an empty covered set, so any config that
  // hits a marker is novel, and an empty coverage is not.
  cv::PassProbeCoverage hit{};
  hit.instcombineAddZero = true;
  assert(cv::isNovelCoverage(hit));

  cv::PassProbeCoverage empty{};
  assert(!cv::isNovelCoverage(empty));

  // Empty covered set: nothing is marked covered.
  assert(!cv::markerCovered("probe.instcombine.add-zero"));
}

void testNuwFlagEmittedInArithmetic() {
  cv::GeneratorConfig config = cv::defaultConfig();
  config.featureBits = 4;  // nuw only, nsw off
  const cv::GeneratedIR generated = cv::generateIR(config);
  assert(generated.moduleText.find("%x = add nuw i32 %a, 0") != std::string::npos);
  assert(generated.moduleText.find("nsw") == std::string::npos);

  cv::GeneratorConfig both = cv::defaultConfig();
  both.featureBits = 5;  // nuw + nsw
  const cv::GeneratedIR bothIR = cv::generateIR(both);
  assert(bothIR.moduleText.find("%x = add nuw nsw i32 %a, 0") != std::string::npos);
}

} // namespace

int main() {
  testDefaultGeneratesAddZero();
  testDiamondGeneratesPhi();
  testSelectVariant();
  testConfigParseNormalizes();
  testSmallConfigConstantsAreStable();
  testNativeSymbolicConfigUsesDefaultConfig();
  testNestedDiamondShape();
  testUnreachableTailShape();
  testSwitchLikeChainShape();
  testAllocaStoreLoadShape();
  testConditionalStoreMemoryShape();
  testIndexedSlotMemoryShape();
  testLoadAfterStoreShape();
  testOverwrittenStoreShape();
  testUnusedAllocaShape();
  testCountedLoopShape();
  testArgumentBoundedLoopShape();
  testEarlyExitLoopShape();
  testInvariantLoopShape();
  testDeadBodyLoopShape();
  testPassProbeFindsInstCombinePatterns();
  testPassProbeFindsCfgPatterns();
  testPassProbeFindsMemoryPatterns();
  testPassProbeFindsLoopPatterns();
  testVectorShapeGeneratesVectorIR();
  testVectorMinMaxAbsShapesGenerateVectorIR();
  testGlobalShapeGeneratesI32Witness();
  testGlobalShapeGeneratesPtrWitness();
  testGlobalShapeGeneratesArrayWitness();
  testPassProbeFindsVectorPatterns();
  testReducerPreservesSelectedMarkers();
  testReducerKeepsUnreachableShape();
  testReducerWithNoMarkersMinimizesToDefaults();
  testReducerPreservesMemoryMarkers();
  testReducerPreservesLoopMarkers();
  testReducerPreservesVectorMarkers();
  testReducerPreservesGlobalMarkers();
  testPassInstrumentationPreservesPredicateValue();
  testPassInstrumentationWritesNativeProbeLog();
  testPassInstrumentationWritesLegacyProbeLog();
  testProbeOracleExactMatch();
  testProbeOracleMissingMarker();
  testProbeOracleUnexpectedMarker();
  testProbeOracleAllowsExtraObserved();
  testProbeOracleEmptyObservedIsNotInstrumented();
  testProbeBackendBoundary();
  testComposeBitsZeroKeepsLegacyOutput();
  testComposeCfgMemoryLoopThreadsValue();
  testComposeSelectsOnlyEnabledDimensions();
  testComposeBitsRoundTripsThroughConfig();
  testComposeVectorAndGlobalIgnoredWithoutBits();
  testComposeVectorThreadsScalar();
  testComposeVectorReductionEmitsDeclare();
  testComposeGlobalAtModuleScope();
  testComposeIntWidthRetypesFunction();
  testComposeIntWidthDefaultIsI32();
  testComposeExtraScalarArgs();
  testComposePointerParamsAliasPattern();
  testComposeCastStageRoundTrips();
  testComposeCastStageWidens();
  testKleeFeedbackNoveltyPredicate();
  testNuwFlagEmittedInArithmetic();
  return 0;
}
