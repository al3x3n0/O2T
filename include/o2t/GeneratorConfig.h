#pragma once

#include <cstdint>
#include <iosfwd>
#include <string>

namespace cv {

enum class ArithOpcode : std::uint8_t {
  Add = 0,
  Sub = 1,
  Mul = 2,
  Xor = 3,
  Or = 4,
  And = 5,
};

enum class RhsMode : std::uint8_t {
  Zero = 0,
  One = 1,
  ArgumentB = 2,
  SmallConstant = 3,
};

enum class ExtraOpcode : std::uint8_t {
  None = 0,
  AddZero = 1,
  MulOne = 2,
  XorSelf = 3,
  DeadAdd = 4,
  AndSelf = 5,
};

enum class Predicate : std::uint8_t {
  Eq = 0,
  Ne = 1,
  Slt = 2,
  Sgt = 3,
};

enum class Shape : std::uint8_t {
  StraightLine = 0,
  Diamond = 1,
  NestedDiamond = 2,
  UnreachableTail = 3,
  SwitchLikeChain = 4,
};

enum class MemoryShape : std::uint8_t {
  None = 0,
  AllocaStoreLoad = 1,
  LoadAfterStore = 2,
  DeadStore = 3,
  OverwrittenStore = 4,
  UnusedAlloca = 5,
};

enum class PointerMode : std::uint8_t {
  DirectSlot = 0,
  SecondSlot = 1,
  IndexedSlot = 2,
};

enum class StoreMode : std::uint8_t {
  SingleStore = 0,
  DoubleStore = 1,
  ConditionalStore = 2,
};

enum class LoadUseMode : std::uint8_t {
  ReturnedLoad = 0,
  ArithmeticUse = 1,
  UnusedLoad = 2,
};

enum class LoopShape : std::uint8_t {
  None = 0,
  CountedLoop = 1,
  EarlyExitLoop = 2,
  InvariantOpLoop = 3,
  DeadBodyLoop = 4,
};

enum class LoopTripMode : std::uint8_t {
  ConstantSmall = 0,
  ArgumentBounded = 1,
  SingleIteration = 2,
};

enum class InductionMode : std::uint8_t {
  IncrementByOne = 0,
  IncrementByConstant = 1,
  Decrement = 2,
};

enum class LoopUseMode : std::uint8_t {
  ReturnPhi = 0,
  ReturnAccumulator = 1,
  UnusedResult = 2,
};

enum class VectorShape : std::uint8_t {
  None = 0,
  AddZero = 1,
  MulOne = 2,
  XorSelf = 3,
  ShuffleIdentity = 4,
  ShuffleSplat = 5,
  ExtractInsert = 6,
  ReductionAddZero = 7,
  SubZero = 8,
  OrZero = 9,
  AndAllOnes = 10,
  InsertExtractIdentity = 11,
  ReductionAddSingleLane = 12,
  ScalableAddZero = 13,
  ScalableMulOne = 14,
  ScalableXorSelf = 15,
  ScalableSubZero = 16,
  ScalableOrZero = 17,
  ScalableAndAllOnes = 18,
  ScalableReductionAddZero = 19,
  SMin = 20,
  SMax = 21,
  UMin = 22,
  UMax = 23,
  Abs = 24,
};

enum class GlobalShape : std::uint8_t {
  None = 0,
  DeadInitializerI32 = 1,
  DeadInitializerPtr = 2,
  DeadInitializerArray = 3,
};

// Integer width for the composed function's threaded scalar/vector/memory/loop
// values. Legacy (composeBits == 0) single-shape output is always i32. The
// small-constant model fits every width, so no constant ever overflows i8.
enum class IntWidth : std::uint8_t {
  I8 = 0,
  I16 = 1,
  I32 = 2,
  I64 = 3,
};

// Bitmask selecting which shape dimensions are composed into a single generated
// function. When zero, the generator falls back to the legacy single-shape
// priority cascade and emits byte-identical IR. When non-zero, the selected
// dimensions are emitted as value-threaded regions, letting CFG, memory, loop,
// and vector shapes co-occur in one `@test` function. A composed global shape is
// emitted at module scope (preserving its dead-initializer semantics) alongside
// the composed function.
enum CompositionBit : std::uint8_t {
  ComposeCfg = 1U << 0U,
  ComposeMemory = 1U << 1U,
  ComposeLoop = 1U << 2U,
  ComposeVector = 1U << 3U,
  ComposeGlobal = 1U << 4U,
  ComposeCast = 1U << 5U,
};

struct GeneratorConfig {
  std::uint8_t arithOpcode;
  std::uint8_t rhsMode;
  std::uint8_t extraOpcode;
  std::uint8_t predicate;
  std::uint8_t shape;
  std::uint8_t featureBits;
  std::uint8_t memoryShape;
  std::uint8_t pointerMode;
  std::uint8_t storeMode;
  std::uint8_t loadUseMode;
  std::uint8_t loopShape;
  std::uint8_t loopTripMode;
  std::uint8_t inductionMode;
  std::uint8_t loopUseMode;
  std::uint8_t vectorShape;
  std::uint8_t globalShape;
  std::uint8_t composeBits;
  std::uint8_t intWidth;
  std::uint8_t scalarArgs;      // index 0..2 -> 2,3,4 integer parameters
  std::uint8_t pointerArgs;     // 0..2 pointer parameters for memory routing
  std::uint8_t pointerNoalias;  // 0/1 -> tag pointer params `noalias`
  std::uint8_t castMode;        // 0..7: bits 0-1 target width, bit 2 signed
  std::int32_t constA;
  std::int32_t constB;
};

struct PatternCoverage {
  bool hasAddZero = false;
  bool hasSubZero = false;
  bool hasMulOne = false;
  bool hasXorSelf = false;
  bool hasOrZero = false;
  bool hasAndAllOnes = false;
  bool hasAndSelf = false;
  bool hasDeadArithmetic = false;
  bool hasBranchDiamond = false;
  bool hasNestedDiamond = false;
  bool hasUnreachableTail = false;
  bool hasSwitchLikeChain = false;
  bool hasPromotableAlloca = false;
  bool hasStoreLoadForward = false;
  bool hasDeadStore = false;
  bool hasOverwrittenStore = false;
  bool hasRedundantLoad = false;
  bool hasUnusedAlloca = false;
  bool hasLoopCanonicalHeader = false;
  bool hasLoopInductionPhi = false;
  bool hasLoopSimpleTripCount = false;
  bool hasLoopInvariantOp = false;
  bool hasDeadLoopInstruction = false;
  bool hasLoopExit = false;
  bool hasVectorAddZero = false;
  bool hasVectorMulOne = false;
  bool hasVectorXorSelf = false;
  bool hasVectorShuffleIdentity = false;
  bool hasVectorShuffleSplat = false;
  bool hasVectorExtractInsert = false;
  bool hasVectorReductionAddZero = false;
  bool hasVectorSubZero = false;
  bool hasVectorOrZero = false;
  bool hasVectorAndAllOnes = false;
  bool hasVectorInsertExtractIdentity = false;
  bool hasVectorReductionAddSingleLane = false;
  bool hasVectorScalableAddZero = false;
  bool hasVectorScalableMulOne = false;
  bool hasVectorScalableXorSelf = false;
  bool hasVectorScalableSubZero = false;
  bool hasVectorScalableOrZero = false;
  bool hasVectorScalableAndAllOnes = false;
  bool hasVectorScalableReductionAddZero = false;
  bool hasVectorSMin = false;
  bool hasVectorSMax = false;
  bool hasVectorUMin = false;
  bool hasVectorUMax = false;
  bool hasVectorAbs = false;
  bool hasGlobalDeadInitializer = false;
};

GeneratorConfig defaultConfig();
GeneratorConfig configFromSeed(std::uint32_t seed);
GeneratorConfig normalizeConfig(GeneratorConfig config);

bool parseConfig(std::istream &input, GeneratorConfig &config, std::string &error);
void writeConfig(std::ostream &output, const GeneratorConfig &config);

const char *toString(ArithOpcode opcode);
const char *toString(RhsMode mode);
const char *toString(ExtraOpcode opcode);
const char *toString(Predicate predicate);
const char *toString(Shape shape);
const char *toString(MemoryShape shape);
const char *toString(PointerMode mode);
const char *toString(StoreMode mode);
const char *toString(LoadUseMode mode);
const char *toString(LoopShape shape);
const char *toString(LoopTripMode mode);
const char *toString(InductionMode mode);
const char *toString(LoopUseMode mode);
const char *toString(VectorShape shape);
const char *toString(GlobalShape shape);
const char *toString(IntWidth width);

ArithOpcode arithOpcode(const GeneratorConfig &config);
RhsMode rhsMode(const GeneratorConfig &config);
ExtraOpcode extraOpcode(const GeneratorConfig &config);
Predicate predicate(const GeneratorConfig &config);
Shape shape(const GeneratorConfig &config);
MemoryShape memoryShape(const GeneratorConfig &config);
PointerMode pointerMode(const GeneratorConfig &config);
StoreMode storeMode(const GeneratorConfig &config);
LoadUseMode loadUseMode(const GeneratorConfig &config);
LoopShape loopShape(const GeneratorConfig &config);
LoopTripMode loopTripMode(const GeneratorConfig &config);
InductionMode inductionMode(const GeneratorConfig &config);
LoopUseMode loopUseMode(const GeneratorConfig &config);
VectorShape vectorShape(const GeneratorConfig &config);
GlobalShape globalShape(const GeneratorConfig &config);
std::uint8_t composeBits(const GeneratorConfig &config);
IntWidth intWidth(const GeneratorConfig &config);
unsigned scalarArgCount(const GeneratorConfig &config);  // 2..4

PatternCoverage coverageFor(const GeneratorConfig &config);

} // namespace cv
