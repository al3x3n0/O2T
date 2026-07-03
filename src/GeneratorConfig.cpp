#include "o2t/GeneratorConfig.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <istream>
#include <ostream>
#include <sstream>
#include <string>

namespace cv {
namespace {

constexpr std::uint8_t kFeatureKeepNsw = 1U << 0U;
constexpr std::uint8_t kFeatureUseSelect = 1U << 1U;
constexpr std::uint8_t kFeatureKeepNuw = 1U << 2U;

std::uint8_t bounded(std::uint8_t value, std::uint8_t limit) {
  return static_cast<std::uint8_t>(value % limit);
}

std::int32_t smallConstant(std::int32_t value) {
  if (value >= -8 && value <= 8) {
    return value;
  }
  return static_cast<std::int32_t>((value % 17 + 17) % 17) - 8;
}

std::uint32_t mix(std::uint32_t x) {
  x ^= x >> 16U;
  x *= 0x7feb352dU;
  x ^= x >> 15U;
  x *= 0x846ca68bU;
  x ^= x >> 16U;
  return x;
}

std::string trim(std::string value) {
  auto notSpace = [](unsigned char c) { return !std::isspace(c); };
  value.erase(value.begin(), std::find_if(value.begin(), value.end(), notSpace));
  value.erase(std::find_if(value.rbegin(), value.rend(), notSpace).base(),
              value.end());
  return value;
}

bool parseInt(const std::string &text, std::int64_t &out) {
  char *end = nullptr;
  const long long parsed = std::strtoll(text.c_str(), &end, 0);
  if (end == text.c_str() || *end != '\0') {
    return false;
  }
  out = static_cast<std::int64_t>(parsed);
  return true;
}

} // namespace

GeneratorConfig defaultConfig() {
  return GeneratorConfig{
      static_cast<std::uint8_t>(ArithOpcode::Add),
      static_cast<std::uint8_t>(RhsMode::Zero),
      static_cast<std::uint8_t>(ExtraOpcode::None),
      static_cast<std::uint8_t>(Predicate::Eq),
      static_cast<std::uint8_t>(Shape::StraightLine),
      kFeatureKeepNsw,
      static_cast<std::uint8_t>(MemoryShape::None),
      static_cast<std::uint8_t>(PointerMode::DirectSlot),
      static_cast<std::uint8_t>(StoreMode::SingleStore),
      static_cast<std::uint8_t>(LoadUseMode::ReturnedLoad),
      static_cast<std::uint8_t>(LoopShape::None),
      static_cast<std::uint8_t>(LoopTripMode::ConstantSmall),
      static_cast<std::uint8_t>(InductionMode::IncrementByOne),
      static_cast<std::uint8_t>(LoopUseMode::ReturnPhi),
      static_cast<std::uint8_t>(VectorShape::None),
      static_cast<std::uint8_t>(GlobalShape::None),
      0,
      static_cast<std::uint8_t>(IntWidth::I32),
      0,
      0,
      0,
      0,
      0,
      1,
  };
}

GeneratorConfig configFromSeed(std::uint32_t seed) {
  GeneratorConfig config{};
  config.arithOpcode = static_cast<std::uint8_t>(mix(seed + 1U));
  config.rhsMode = static_cast<std::uint8_t>(mix(seed + 2U));
  config.extraOpcode = static_cast<std::uint8_t>(mix(seed + 3U));
  config.predicate = static_cast<std::uint8_t>(mix(seed + 4U));
  config.shape = static_cast<std::uint8_t>(mix(seed + 5U));
  config.featureBits = static_cast<std::uint8_t>(mix(seed + 6U));
  config.memoryShape = static_cast<std::uint8_t>(mix(seed + 7U));
  config.pointerMode = static_cast<std::uint8_t>(mix(seed + 8U));
  config.storeMode = static_cast<std::uint8_t>(mix(seed + 9U));
  config.loadUseMode = static_cast<std::uint8_t>(mix(seed + 10U));
  config.loopShape = static_cast<std::uint8_t>(mix(seed + 11U));
  config.loopTripMode = static_cast<std::uint8_t>(mix(seed + 12U));
  config.inductionMode = static_cast<std::uint8_t>(mix(seed + 13U));
  config.loopUseMode = static_cast<std::uint8_t>(mix(seed + 14U));
  config.vectorShape = static_cast<std::uint8_t>(mix(seed + 17U));
  config.globalShape = static_cast<std::uint8_t>(mix(seed + 18U));
  // Seeds explore composition too: ~7/8 of seeds compose CFG/memory/loop
  // regions (composeBits != 0); the rest exercise the legacy single-shape path.
  // Seeds that land on a vector/global shape fall back to the legacy cascade.
  config.composeBits = static_cast<std::uint8_t>(mix(seed + 19U));
  config.intWidth = static_cast<std::uint8_t>(mix(seed + 20U));
  config.scalarArgs = static_cast<std::uint8_t>(mix(seed + 21U));
  config.pointerArgs = static_cast<std::uint8_t>(mix(seed + 22U));
  config.pointerNoalias = static_cast<std::uint8_t>(mix(seed + 23U));
  config.castMode = static_cast<std::uint8_t>(mix(seed + 24U));
  config.constA = static_cast<std::int32_t>(mix(seed + 15U));
  config.constB = static_cast<std::int32_t>(mix(seed + 16U));
  return normalizeConfig(config);
}

GeneratorConfig normalizeConfig(GeneratorConfig config) {
  config.arithOpcode = bounded(config.arithOpcode, 6);
  config.rhsMode = bounded(config.rhsMode, 4);
  config.extraOpcode = bounded(config.extraOpcode, 6);
  config.predicate = bounded(config.predicate, 4);
  config.shape = bounded(config.shape, 5);
  config.featureBits &= static_cast<std::uint8_t>(kFeatureKeepNsw |
                                                  kFeatureUseSelect |
                                                  kFeatureKeepNuw);
  config.memoryShape = bounded(config.memoryShape, 6);
  config.pointerMode = bounded(config.pointerMode, 3);
  config.storeMode = bounded(config.storeMode, 3);
  config.loadUseMode = bounded(config.loadUseMode, 3);
  config.loopShape = bounded(config.loopShape, 5);
  config.loopTripMode = bounded(config.loopTripMode, 3);
  config.inductionMode = bounded(config.inductionMode, 3);
  config.loopUseMode = bounded(config.loopUseMode, 3);
  config.vectorShape = bounded(config.vectorShape, 25);
  config.globalShape = bounded(config.globalShape, 4);
  config.composeBits = bounded(config.composeBits, 64);
  config.intWidth = bounded(config.intWidth, 4);
  config.castMode = bounded(config.castMode, 8);
  config.scalarArgs = bounded(config.scalarArgs, 3);
  config.pointerArgs = bounded(config.pointerArgs, 3);
  config.pointerNoalias = bounded(config.pointerNoalias, 2);
  config.constA = smallConstant(config.constA);
  config.constB = smallConstant(config.constB);
  return config;
}

bool parseConfig(std::istream &input, GeneratorConfig &config, std::string &error) {
  config = defaultConfig();
  std::string line;
  unsigned lineNumber = 0;
  while (std::getline(input, line)) {
    ++lineNumber;
    const auto comment = line.find('#');
    if (comment != std::string::npos) {
      line.erase(comment);
    }
    line = trim(line);
    if (line.empty()) {
      continue;
    }

    const auto equals = line.find('=');
    if (equals == std::string::npos) {
      std::ostringstream message;
      message << "line " << lineNumber << ": expected key=value";
      error = message.str();
      return false;
    }

    const std::string key = trim(line.substr(0, equals));
    const std::string valueText = trim(line.substr(equals + 1));
    std::int64_t value = 0;
    if (!parseInt(valueText, value)) {
      std::ostringstream message;
      message << "line " << lineNumber << ": invalid integer '" << valueText << "'";
      error = message.str();
      return false;
    }

    if (key == "arith_opcode") {
      config.arithOpcode = static_cast<std::uint8_t>(value);
    } else if (key == "rhs_mode") {
      config.rhsMode = static_cast<std::uint8_t>(value);
    } else if (key == "extra_opcode") {
      config.extraOpcode = static_cast<std::uint8_t>(value);
    } else if (key == "predicate") {
      config.predicate = static_cast<std::uint8_t>(value);
    } else if (key == "shape") {
      config.shape = static_cast<std::uint8_t>(value);
    } else if (key == "feature_bits") {
      config.featureBits = static_cast<std::uint8_t>(value);
    } else if (key == "memory_shape") {
      config.memoryShape = static_cast<std::uint8_t>(value);
    } else if (key == "pointer_mode") {
      config.pointerMode = static_cast<std::uint8_t>(value);
    } else if (key == "store_mode") {
      config.storeMode = static_cast<std::uint8_t>(value);
    } else if (key == "load_use_mode") {
      config.loadUseMode = static_cast<std::uint8_t>(value);
    } else if (key == "loop_shape") {
      config.loopShape = static_cast<std::uint8_t>(value);
    } else if (key == "loop_trip_mode") {
      config.loopTripMode = static_cast<std::uint8_t>(value);
    } else if (key == "induction_mode") {
      config.inductionMode = static_cast<std::uint8_t>(value);
    } else if (key == "loop_use_mode") {
      config.loopUseMode = static_cast<std::uint8_t>(value);
    } else if (key == "vector_shape") {
      config.vectorShape = static_cast<std::uint8_t>(value);
    } else if (key == "global_shape") {
      config.globalShape = static_cast<std::uint8_t>(value);
    } else if (key == "compose_bits") {
      config.composeBits = static_cast<std::uint8_t>(value);
    } else if (key == "int_width") {
      config.intWidth = static_cast<std::uint8_t>(value);
    } else if (key == "scalar_args") {
      config.scalarArgs = static_cast<std::uint8_t>(value);
    } else if (key == "pointer_args") {
      config.pointerArgs = static_cast<std::uint8_t>(value);
    } else if (key == "pointer_noalias") {
      config.pointerNoalias = static_cast<std::uint8_t>(value);
    } else if (key == "cast_mode") {
      config.castMode = static_cast<std::uint8_t>(value);
    } else if (key == "const_a") {
      config.constA = static_cast<std::int32_t>(value);
    } else if (key == "const_b") {
      config.constB = static_cast<std::int32_t>(value);
    } else {
      std::ostringstream message;
      message << "line " << lineNumber << ": unknown key '" << key << "'";
      error = message.str();
      return false;
    }
  }

  config = normalizeConfig(config);
  return true;
}

void writeConfig(std::ostream &output, const GeneratorConfig &rawConfig) {
  const GeneratorConfig config = normalizeConfig(rawConfig);
  output << "arith_opcode=" << static_cast<unsigned>(config.arithOpcode) << '\n';
  output << "rhs_mode=" << static_cast<unsigned>(config.rhsMode) << '\n';
  output << "extra_opcode=" << static_cast<unsigned>(config.extraOpcode) << '\n';
  output << "predicate=" << static_cast<unsigned>(config.predicate) << '\n';
  output << "shape=" << static_cast<unsigned>(config.shape) << '\n';
  output << "feature_bits=" << static_cast<unsigned>(config.featureBits) << '\n';
  output << "memory_shape=" << static_cast<unsigned>(config.memoryShape) << '\n';
  output << "pointer_mode=" << static_cast<unsigned>(config.pointerMode) << '\n';
  output << "store_mode=" << static_cast<unsigned>(config.storeMode) << '\n';
  output << "load_use_mode=" << static_cast<unsigned>(config.loadUseMode) << '\n';
  output << "loop_shape=" << static_cast<unsigned>(config.loopShape) << '\n';
  output << "loop_trip_mode=" << static_cast<unsigned>(config.loopTripMode) << '\n';
  output << "induction_mode=" << static_cast<unsigned>(config.inductionMode) << '\n';
  output << "loop_use_mode=" << static_cast<unsigned>(config.loopUseMode) << '\n';
  output << "vector_shape=" << static_cast<unsigned>(config.vectorShape) << '\n';
  output << "global_shape=" << static_cast<unsigned>(config.globalShape) << '\n';
  output << "compose_bits=" << static_cast<unsigned>(config.composeBits) << '\n';
  output << "int_width=" << static_cast<unsigned>(config.intWidth) << '\n';
  output << "scalar_args=" << static_cast<unsigned>(config.scalarArgs) << '\n';
  output << "pointer_args=" << static_cast<unsigned>(config.pointerArgs) << '\n';
  output << "pointer_noalias=" << static_cast<unsigned>(config.pointerNoalias)
         << '\n';
  output << "cast_mode=" << static_cast<unsigned>(config.castMode) << '\n';
  output << "const_a=" << config.constA << '\n';
  output << "const_b=" << config.constB << '\n';
}

const char *toString(ArithOpcode opcode) {
  switch (opcode) {
  case ArithOpcode::Add:
    return "add";
  case ArithOpcode::Sub:
    return "sub";
  case ArithOpcode::Mul:
    return "mul";
  case ArithOpcode::Xor:
    return "xor";
  case ArithOpcode::Or:
    return "or";
  case ArithOpcode::And:
    return "and";
  }
  return "add";
}

const char *toString(RhsMode mode) {
  switch (mode) {
  case RhsMode::Zero:
    return "zero";
  case RhsMode::One:
    return "one";
  case RhsMode::ArgumentB:
    return "argument-b";
  case RhsMode::SmallConstant:
    return "small-constant";
  }
  return "zero";
}

const char *toString(ExtraOpcode opcode) {
  switch (opcode) {
  case ExtraOpcode::None:
    return "none";
  case ExtraOpcode::AddZero:
    return "add-zero";
  case ExtraOpcode::MulOne:
    return "mul-one";
  case ExtraOpcode::XorSelf:
    return "xor-self";
  case ExtraOpcode::DeadAdd:
    return "dead-add";
  case ExtraOpcode::AndSelf:
    return "and-self";
  }
  return "none";
}

const char *toString(Predicate predicate) {
  switch (predicate) {
  case Predicate::Eq:
    return "eq";
  case Predicate::Ne:
    return "ne";
  case Predicate::Slt:
    return "slt";
  case Predicate::Sgt:
    return "sgt";
  }
  return "eq";
}

const char *toString(Shape shape) {
  switch (shape) {
  case Shape::StraightLine:
    return "straight-line";
  case Shape::Diamond:
    return "diamond";
  case Shape::NestedDiamond:
    return "nested-diamond";
  case Shape::UnreachableTail:
    return "unreachable-tail";
  case Shape::SwitchLikeChain:
    return "switch-like-chain";
  }
  return "straight-line";
}

const char *toString(MemoryShape shape) {
  switch (shape) {
  case MemoryShape::None:
    return "none";
  case MemoryShape::AllocaStoreLoad:
    return "alloca-store-load";
  case MemoryShape::LoadAfterStore:
    return "load-after-store";
  case MemoryShape::DeadStore:
    return "dead-store";
  case MemoryShape::OverwrittenStore:
    return "overwritten-store";
  case MemoryShape::UnusedAlloca:
    return "unused-alloca";
  }
  return "none";
}

const char *toString(PointerMode mode) {
  switch (mode) {
  case PointerMode::DirectSlot:
    return "direct-slot";
  case PointerMode::SecondSlot:
    return "second-slot";
  case PointerMode::IndexedSlot:
    return "indexed-slot";
  }
  return "direct-slot";
}

const char *toString(StoreMode mode) {
  switch (mode) {
  case StoreMode::SingleStore:
    return "single-store";
  case StoreMode::DoubleStore:
    return "double-store";
  case StoreMode::ConditionalStore:
    return "conditional-store";
  }
  return "single-store";
}

const char *toString(LoadUseMode mode) {
  switch (mode) {
  case LoadUseMode::ReturnedLoad:
    return "returned-load";
  case LoadUseMode::ArithmeticUse:
    return "arithmetic-use";
  case LoadUseMode::UnusedLoad:
    return "unused-load";
  }
  return "returned-load";
}

const char *toString(LoopShape shape) {
  switch (shape) {
  case LoopShape::None:
    return "none";
  case LoopShape::CountedLoop:
    return "counted-loop";
  case LoopShape::EarlyExitLoop:
    return "early-exit-loop";
  case LoopShape::InvariantOpLoop:
    return "invariant-op-loop";
  case LoopShape::DeadBodyLoop:
    return "dead-body-loop";
  }
  return "none";
}

const char *toString(LoopTripMode mode) {
  switch (mode) {
  case LoopTripMode::ConstantSmall:
    return "constant-small";
  case LoopTripMode::ArgumentBounded:
    return "argument-bounded";
  case LoopTripMode::SingleIteration:
    return "single-iteration";
  }
  return "constant-small";
}

const char *toString(InductionMode mode) {
  switch (mode) {
  case InductionMode::IncrementByOne:
    return "increment-by-one";
  case InductionMode::IncrementByConstant:
    return "increment-by-constant";
  case InductionMode::Decrement:
    return "decrement";
  }
  return "increment-by-one";
}

const char *toString(LoopUseMode mode) {
  switch (mode) {
  case LoopUseMode::ReturnPhi:
    return "return-phi";
  case LoopUseMode::ReturnAccumulator:
    return "return-accumulator";
  case LoopUseMode::UnusedResult:
    return "unused-result";
  }
  return "return-phi";
}

const char *toString(VectorShape shape) {
  switch (shape) {
  case VectorShape::None:
    return "none";
  case VectorShape::AddZero:
    return "add-zero";
  case VectorShape::MulOne:
    return "mul-one";
  case VectorShape::XorSelf:
    return "xor-self";
  case VectorShape::ShuffleIdentity:
    return "shuffle-identity";
  case VectorShape::ShuffleSplat:
    return "shuffle-splat";
  case VectorShape::ExtractInsert:
    return "extract-insert";
  case VectorShape::ReductionAddZero:
    return "reduction-add-zero";
  case VectorShape::SubZero:
    return "sub-zero";
  case VectorShape::OrZero:
    return "or-zero";
  case VectorShape::AndAllOnes:
    return "and-allones";
  case VectorShape::InsertExtractIdentity:
    return "insert-extract-identity";
  case VectorShape::ReductionAddSingleLane:
    return "reduction-add-single-lane";
  case VectorShape::ScalableAddZero:
    return "scalable-add-zero";
  case VectorShape::ScalableMulOne:
    return "scalable-mul-one";
  case VectorShape::ScalableXorSelf:
    return "scalable-xor-self";
  case VectorShape::ScalableSubZero:
    return "scalable-sub-zero";
  case VectorShape::ScalableOrZero:
    return "scalable-or-zero";
  case VectorShape::ScalableAndAllOnes:
    return "scalable-and-allones";
  case VectorShape::ScalableReductionAddZero:
    return "scalable-reduction-add-zero";
  case VectorShape::SMin:
    return "smin";
  case VectorShape::SMax:
    return "smax";
  case VectorShape::UMin:
    return "umin";
  case VectorShape::UMax:
    return "umax";
  case VectorShape::Abs:
    return "abs";
  }
  return "none";
}

const char *toString(IntWidth width) {
  switch (width) {
  case IntWidth::I8:
    return "i8";
  case IntWidth::I16:
    return "i16";
  case IntWidth::I32:
    return "i32";
  case IntWidth::I64:
    return "i64";
  }
  return "i32";
}

const char *toString(GlobalShape shape) {
  switch (shape) {
  case GlobalShape::None:
    return "none";
  case GlobalShape::DeadInitializerI32:
    return "dead-initializer-i32";
  case GlobalShape::DeadInitializerPtr:
    return "dead-initializer-ptr";
  case GlobalShape::DeadInitializerArray:
    return "dead-initializer-array";
  }
  return "none";
}

ArithOpcode arithOpcode(const GeneratorConfig &config) {
  return static_cast<ArithOpcode>(normalizeConfig(config).arithOpcode);
}

RhsMode rhsMode(const GeneratorConfig &config) {
  return static_cast<RhsMode>(normalizeConfig(config).rhsMode);
}

ExtraOpcode extraOpcode(const GeneratorConfig &config) {
  return static_cast<ExtraOpcode>(normalizeConfig(config).extraOpcode);
}

Predicate predicate(const GeneratorConfig &config) {
  return static_cast<Predicate>(normalizeConfig(config).predicate);
}

Shape shape(const GeneratorConfig &config) {
  return static_cast<Shape>(normalizeConfig(config).shape);
}

MemoryShape memoryShape(const GeneratorConfig &config) {
  return static_cast<MemoryShape>(normalizeConfig(config).memoryShape);
}

PointerMode pointerMode(const GeneratorConfig &config) {
  return static_cast<PointerMode>(normalizeConfig(config).pointerMode);
}

StoreMode storeMode(const GeneratorConfig &config) {
  return static_cast<StoreMode>(normalizeConfig(config).storeMode);
}

LoadUseMode loadUseMode(const GeneratorConfig &config) {
  return static_cast<LoadUseMode>(normalizeConfig(config).loadUseMode);
}

LoopShape loopShape(const GeneratorConfig &config) {
  return static_cast<LoopShape>(normalizeConfig(config).loopShape);
}

LoopTripMode loopTripMode(const GeneratorConfig &config) {
  return static_cast<LoopTripMode>(normalizeConfig(config).loopTripMode);
}

InductionMode inductionMode(const GeneratorConfig &config) {
  return static_cast<InductionMode>(normalizeConfig(config).inductionMode);
}

LoopUseMode loopUseMode(const GeneratorConfig &config) {
  return static_cast<LoopUseMode>(normalizeConfig(config).loopUseMode);
}

VectorShape vectorShape(const GeneratorConfig &config) {
  return static_cast<VectorShape>(normalizeConfig(config).vectorShape);
}

GlobalShape globalShape(const GeneratorConfig &config) {
  return static_cast<GlobalShape>(normalizeConfig(config).globalShape);
}

std::uint8_t composeBits(const GeneratorConfig &config) {
  return normalizeConfig(config).composeBits;
}

IntWidth intWidth(const GeneratorConfig &config) {
  return static_cast<IntWidth>(normalizeConfig(config).intWidth);
}

unsigned scalarArgCount(const GeneratorConfig &config) {
  return 2U + normalizeConfig(config).scalarArgs;  // index 0..2 -> 2..4
}

namespace {

void applyVectorCoverage(PatternCoverage &coverage, VectorShape vecShape) {
  coverage.hasVectorAddZero = vecShape == VectorShape::AddZero;
  coverage.hasVectorMulOne = vecShape == VectorShape::MulOne;
  coverage.hasVectorXorSelf = vecShape == VectorShape::XorSelf;
  coverage.hasVectorShuffleIdentity = vecShape == VectorShape::ShuffleIdentity;
  coverage.hasVectorShuffleSplat = vecShape == VectorShape::ShuffleSplat;
  coverage.hasVectorExtractInsert = vecShape == VectorShape::ExtractInsert;
  coverage.hasVectorReductionAddZero = vecShape == VectorShape::ReductionAddZero;
  coverage.hasVectorSubZero = vecShape == VectorShape::SubZero;
  coverage.hasVectorOrZero = vecShape == VectorShape::OrZero;
  coverage.hasVectorAndAllOnes = vecShape == VectorShape::AndAllOnes;
  coverage.hasVectorInsertExtractIdentity =
      vecShape == VectorShape::InsertExtractIdentity;
  coverage.hasVectorReductionAddSingleLane =
      vecShape == VectorShape::ReductionAddSingleLane;
  coverage.hasVectorScalableAddZero = vecShape == VectorShape::ScalableAddZero;
  coverage.hasVectorScalableMulOne = vecShape == VectorShape::ScalableMulOne;
  coverage.hasVectorScalableXorSelf = vecShape == VectorShape::ScalableXorSelf;
  coverage.hasVectorScalableSubZero = vecShape == VectorShape::ScalableSubZero;
  coverage.hasVectorScalableOrZero = vecShape == VectorShape::ScalableOrZero;
  coverage.hasVectorScalableAndAllOnes =
      vecShape == VectorShape::ScalableAndAllOnes;
  coverage.hasVectorScalableReductionAddZero =
      vecShape == VectorShape::ScalableReductionAddZero;
  coverage.hasVectorSMin = vecShape == VectorShape::SMin;
  coverage.hasVectorSMax = vecShape == VectorShape::SMax;
  coverage.hasVectorUMin = vecShape == VectorShape::UMin;
  coverage.hasVectorUMax = vecShape == VectorShape::UMax;
  coverage.hasVectorAbs = vecShape == VectorShape::Abs;
}

} // namespace

PatternCoverage coverageFor(const GeneratorConfig &rawConfig) {
  const GeneratorConfig config = normalizeConfig(rawConfig);
  const auto globShape = globalShape(config);
  const auto vecShape = vectorShape(config);
  const bool composed = config.composeBits != 0;

  // Legacy single-shape mode reports exactly the one dimension the cascade
  // emits. Composed mode unions every non-None dimension (the "config implies"
  // model), so vector/global markers join the scalar/cfg/memory/loop set.
  if (!composed) {
    if (globShape != GlobalShape::None) {
      PatternCoverage coverage;
      coverage.hasGlobalDeadInitializer = true;
      return coverage;
    }
    if (vecShape != VectorShape::None) {
      PatternCoverage coverage;
      applyVectorCoverage(coverage, vecShape);
      return coverage;
    }
  }

  const auto op = arithOpcode(config);
  const auto rhs = rhsMode(config);
  const auto extra = extraOpcode(config);

  PatternCoverage coverage;
  coverage.hasAddZero = (op == ArithOpcode::Add && rhs == RhsMode::Zero) ||
                        extra == ExtraOpcode::AddZero;
  coverage.hasSubZero = op == ArithOpcode::Sub && rhs == RhsMode::Zero;
  coverage.hasMulOne = (op == ArithOpcode::Mul && rhs == RhsMode::One) ||
                       extra == ExtraOpcode::MulOne;
  coverage.hasXorSelf = extra == ExtraOpcode::XorSelf;
  coverage.hasOrZero = op == ArithOpcode::Or && rhs == RhsMode::Zero;
  coverage.hasAndAllOnes =
      op == ArithOpcode::And && rhs == RhsMode::SmallConstant &&
      config.constA == -1;
  coverage.hasAndSelf = extra == ExtraOpcode::AndSelf;
  coverage.hasDeadArithmetic = extra == ExtraOpcode::DeadAdd;
  const auto cfgShape = shape(config);
  coverage.hasBranchDiamond = cfgShape == Shape::Diamond;
  coverage.hasNestedDiamond = cfgShape == Shape::NestedDiamond;
  coverage.hasUnreachableTail = cfgShape == Shape::UnreachableTail;
  coverage.hasSwitchLikeChain = cfgShape == Shape::SwitchLikeChain;
  const auto memShape = memoryShape(config);
  coverage.hasPromotableAlloca = memShape == MemoryShape::AllocaStoreLoad ||
                                 memShape == MemoryShape::LoadAfterStore;
  coverage.hasStoreLoadForward = memShape == MemoryShape::AllocaStoreLoad ||
                                 memShape == MemoryShape::LoadAfterStore;
  coverage.hasDeadStore = memShape == MemoryShape::DeadStore;
  coverage.hasOverwrittenStore = memShape == MemoryShape::OverwrittenStore;
  coverage.hasRedundantLoad = memShape == MemoryShape::LoadAfterStore;
  coverage.hasUnusedAlloca = memShape == MemoryShape::UnusedAlloca;
  const auto lpShape = loopShape(config);
  coverage.hasLoopCanonicalHeader = lpShape != LoopShape::None;
  coverage.hasLoopInductionPhi = lpShape != LoopShape::None;
  coverage.hasLoopSimpleTripCount = lpShape != LoopShape::None;
  coverage.hasLoopInvariantOp = lpShape == LoopShape::InvariantOpLoop;
  coverage.hasDeadLoopInstruction = lpShape == LoopShape::DeadBodyLoop;
  coverage.hasLoopExit = lpShape == LoopShape::EarlyExitLoop ||
                         lpShape == LoopShape::CountedLoop ||
                         lpShape == LoopShape::InvariantOpLoop ||
                         lpShape == LoopShape::DeadBodyLoop;

  if (composed) {
    if (vecShape != VectorShape::None) {
      applyVectorCoverage(coverage, vecShape);
    }
    if (globShape != GlobalShape::None) {
      coverage.hasGlobalDeadInitializer = true;
    }
  }
  return coverage;
}

} // namespace cv
