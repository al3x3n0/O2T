#include "o2t/IRTextGenerator.h"

#include <sstream>
#include <string>

namespace cv {
namespace {

bool hasNsw(const GeneratorConfig &config) {
  return (config.featureBits & 1U) != 0U;
}

bool useSelect(const GeneratorConfig &config) {
  return (config.featureBits & 2U) != 0U;
}

bool hasNuw(const GeneratorConfig &config) {
  return (config.featureBits & 4U) != 0U;
}

// Element type for the composed function. Legacy single-shape output is i32.
std::string intTy(const GeneratorConfig &config) {
  return toString(intWidth(config));
}

// Scalar parameter names: %a, %b, %c, %d (index 0..3).
std::string scalarArgName(unsigned index) {
  return std::string("%") + static_cast<char>('a' + static_cast<int>(index));
}

// Pointer parameter names: %p, %q (index 0..1).
std::string pointerArgName(unsigned index) {
  return std::string("%") + static_cast<char>('p' + static_cast<int>(index));
}

// Parameter list inside the parens of the composed function signature.
std::string composedSignature(const GeneratorConfig &config,
                              const std::string &ty) {
  std::string sig;
  const unsigned scalars = scalarArgCount(config);
  for (unsigned i = 0; i < scalars; ++i) {
    if (!sig.empty()) {
      sig += ", ";
    }
    sig += ty + " " + scalarArgName(i);
  }
  const char *noalias = config.pointerNoalias != 0U ? "noalias " : "";
  for (unsigned j = 0; j < config.pointerArgs; ++j) {
    sig += ", ptr " + std::string(noalias) + pointerArgName(j);
  }
  return sig;
}

std::string rhsValue(const GeneratorConfig &config) {
  switch (rhsMode(config)) {
  case RhsMode::Zero:
    return "0";
  case RhsMode::One:
    return "1";
  case RhsMode::ArgumentB:
    return "%b";
  case RhsMode::SmallConstant:
    return std::to_string(config.constA);
  }
  return "0";
}

std::string arithmeticInstruction(const char *name, ArithOpcode opcode,
                                  const std::string &lhs, const std::string &rhs,
                                  bool nsw, bool nuw, const std::string &ty) {
  std::ostringstream out;
  out << "  " << name << " = " << toString(opcode);
  if (opcode == ArithOpcode::Add || opcode == ArithOpcode::Sub ||
      opcode == ArithOpcode::Mul) {
    // LLVM canonical order is `nuw nsw`; both flags are independently valid.
    if (nuw) {
      out << " nuw";
    }
    if (nsw) {
      out << " nsw";
    }
  }
  out << " " << ty << " " << lhs << ", " << rhs << "\n";
  return out.str();
}

// Legacy convenience: i32 element type, flags read from the config.
std::string arithmeticInstruction(const char *name, ArithOpcode opcode,
                                  const std::string &lhs, const std::string &rhs,
                                  const GeneratorConfig &config) {
  return arithmeticInstruction(name, opcode, lhs, rhs, hasNsw(config),
                               hasNuw(config), "i32");
}

void emitStackSlot(std::ostream &out, const GeneratorConfig &config) {
  switch (pointerMode(config)) {
  case PointerMode::DirectSlot:
    out << "  %slot = alloca i32, align 4\n";
    return;
  case PointerMode::SecondSlot:
    out << "  %slot = alloca i32, align 4\n";
    out << "  %slot.b = alloca i32, align 4\n";
    return;
  case PointerMode::IndexedSlot:
    out << "  %slots = alloca [2 x i32], align 4\n";
    out << "  %slot = getelementptr inbounds [2 x i32], ptr %slots, i32 0, i32 0\n";
    return;
  }
}

std::string activeSlot(const GeneratorConfig &config) {
  return pointerMode(config) == PointerMode::SecondSlot ? "%slot.b" : "%slot";
}

int positiveSmall(std::int32_t value, int fallback, int modulo) {
  int normalized = static_cast<int>(value % modulo);
  if (normalized < 0) {
    normalized += modulo;
  }
  return normalized == 0 ? fallback : normalized;
}

void emitLoadUse(std::ostream &out, const GeneratorConfig &config,
                 const std::string &loadName) {
  switch (loadUseMode(config)) {
  case LoadUseMode::ReturnedLoad:
    out << "  ret i32 " << loadName << "\n";
    return;
  case LoadUseMode::ArithmeticUse:
    out << "  %use = add i32 " << loadName << ", %b\n";
    out << "  ret i32 %use\n";
    return;
  case LoadUseMode::UnusedLoad:
    out << "  ret i32 %a\n";
    return;
  }
}

void emitAllocaStoreLoad(std::ostream &out, const GeneratorConfig &config) {
  emitStackSlot(out, config);
  const std::string slot = activeSlot(config);

  if (storeMode(config) == StoreMode::ConditionalStore) {
    out << "  %mem.cmp = icmp " << toString(predicate(config)) << " i32 %a, %b\n";
    out << "  br i1 %mem.cmp, label %store.then, label %store.else\n\n";
    out << "store.then:\n";
    out << "  store i32 %a, ptr " << slot << ", align 4\n";
    out << "  br label %store.merge\n\n";
    out << "store.else:\n";
    out << "  store i32 " << config.constA << ", ptr " << slot << ", align 4\n";
    out << "  br label %store.merge\n\n";
    out << "store.merge:\n";
  } else {
    out << "  store i32 %a, ptr " << slot << ", align 4\n";
  }

  if (storeMode(config) == StoreMode::DoubleStore) {
    out << "  store i32 " << config.constB << ", ptr " << slot << ", align 4\n";
  }

  out << "  %loaded = load i32, ptr " << slot << ", align 4\n";
  emitLoadUse(out, config, "%loaded");
}

void emitLoadAfterStore(std::ostream &out, const GeneratorConfig &config) {
  emitStackSlot(out, config);
  const std::string slot = activeSlot(config);
  out << "  store i32 %a, ptr " << slot << ", align 4\n";
  out << "  %loaded = load i32, ptr " << slot << ", align 4\n";
  out << "  %loaded.again = load i32, ptr " << slot << ", align 4\n";
  if (loadUseMode(config) == LoadUseMode::ArithmeticUse) {
    out << "  %use = add i32 %loaded, %loaded.again\n";
    out << "  ret i32 %use\n";
  } else if (loadUseMode(config) == LoadUseMode::UnusedLoad) {
    out << "  ret i32 %loaded\n";
  } else {
    out << "  ret i32 %loaded.again\n";
  }
}

void emitDeadStore(std::ostream &out, const GeneratorConfig &config) {
  emitStackSlot(out, config);
  out << "  store i32 " << config.constA << ", ptr " << activeSlot(config)
      << ", align 4\n";
  out << "  ret i32 %a\n";
}

void emitOverwrittenStore(std::ostream &out, const GeneratorConfig &config) {
  emitStackSlot(out, config);
  const std::string slot = activeSlot(config);
  out << "  store i32 " << config.constA << ", ptr " << slot << ", align 4\n";
  out << "  store i32 %a, ptr " << slot << ", align 4\n";
  out << "  %loaded = load i32, ptr " << slot << ", align 4\n";
  emitLoadUse(out, config, "%loaded");
}

void emitUnusedAlloca(std::ostream &out, const GeneratorConfig &config) {
  emitStackSlot(out, config);
  out << "  ret i32 %a\n";
}

std::string emitLoopLimit(std::ostream &out, const GeneratorConfig &config) {
  switch (loopTripMode(config)) {
  case LoopTripMode::ConstantSmall:
    return std::to_string(positiveSmall(config.constA, 4, 8));
  case LoopTripMode::ArgumentBounded:
    out << "  %limit.mask = and i32 %b, 7\n";
    out << "  %limit = add i32 %limit.mask, 1\n";
    return "%limit";
  case LoopTripMode::SingleIteration:
    return "1";
  }
  return "4";
}

std::string inductionStart(const GeneratorConfig &config,
                           const std::string &limit) {
  return inductionMode(config) == InductionMode::Decrement ? limit : "0";
}

std::string inductionPredicate(const GeneratorConfig &config) {
  return inductionMode(config) == InductionMode::Decrement ? "sgt" : "slt";
}

std::string inductionCompareRhs(const GeneratorConfig &config,
                                const std::string &limit) {
  return inductionMode(config) == InductionMode::Decrement ? "0" : limit;
}

std::string inductionStep(const GeneratorConfig &config) {
  switch (inductionMode(config)) {
  case InductionMode::IncrementByOne:
    return "1";
  case InductionMode::IncrementByConstant:
    return std::to_string(positiveSmall(config.constB, 2, 4));
  case InductionMode::Decrement:
    return "-1";
  }
  return "1";
}

void emitLoopReturn(std::ostream &out, const GeneratorConfig &config) {
  switch (loopUseMode(config)) {
  case LoopUseMode::ReturnPhi:
    out << "  ret i32 %i\n";
    return;
  case LoopUseMode::ReturnAccumulator:
    out << "  ret i32 %acc\n";
    return;
  case LoopUseMode::UnusedResult:
    out << "  ret i32 %a\n";
    return;
  }
}

void emitLoop(std::ostream &out, const GeneratorConfig &config) {
  const std::string limit = emitLoopLimit(out, config);
  const std::string start = inductionStart(config, limit);
  out << "  br label %loop.header\n\n";

  out << "loop.header:\n";
  out << "  %i = phi i32 [ " << start << ", %entry ], [ %next, %loop.latch ]\n";
  out << "  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop.latch ]\n";
  out << "  %loop.cond = icmp " << inductionPredicate(config) << " i32 %i, "
      << inductionCompareRhs(config, limit) << "\n";
  out << "  br i1 %loop.cond, label %loop.body, label %loop.exit\n\n";

  out << "loop.body:\n";
  if (loopShape(config) == LoopShape::InvariantOpLoop) {
    out << "  %invariant = add i32 %a, " << config.constB << "\n";
    out << "  %body.v = add i32 %invariant, 1\n";
  } else {
    out << arithmeticInstruction("%body.v", arithOpcode(config), "%i",
                                 rhsValue(config), config);
  }
  if (loopShape(config) == LoopShape::DeadBodyLoop) {
    out << "  %loop.dead = add i32 %a, " << config.constB << "\n";
  }
  if (loopShape(config) == LoopShape::EarlyExitLoop) {
    out << "  %early.exit = icmp eq i32 %body.v, " << config.constA << "\n";
    out << "  br i1 %early.exit, label %loop.exit, label %loop.latch\n\n";
  } else {
    out << "  br label %loop.latch\n\n";
  }

  out << "loop.latch:\n";
  out << "  %next = add i32 %i, " << inductionStep(config) << "\n";
  out << "  %acc.next = add i32 %acc, %body.v\n";
  out << "  br label %loop.header\n\n";

  out << "loop.exit:\n";
  emitLoopReturn(out, config);
}

void emitExtra(std::ostream &out, const GeneratorConfig &config,
               std::string &currentValue) {
  switch (extraOpcode(config)) {
  case ExtraOpcode::None:
    return;
  case ExtraOpcode::AddZero:
    out << "  %extra = add";
    if (hasNuw(config)) {
      out << " nuw";
    }
    if (hasNsw(config)) {
      out << " nsw";
    }
    out << " i32 " << currentValue << ", 0\n";
    currentValue = "%extra";
    return;
  case ExtraOpcode::MulOne:
    out << "  %extra = mul";
    if (hasNuw(config)) {
      out << " nuw";
    }
    if (hasNsw(config)) {
      out << " nsw";
    }
    out << " i32 " << currentValue << ", 1\n";
    currentValue = "%extra";
    return;
  case ExtraOpcode::XorSelf:
    out << "  %extra = xor i32 " << currentValue << ", " << currentValue << "\n";
    currentValue = "%extra";
    return;
  case ExtraOpcode::DeadAdd:
    out << "  %dead = add i32 %a, " << config.constB << "\n";
    return;
  case ExtraOpcode::AndSelf:
    out << "  %extra = and i32 " << currentValue << ", " << currentValue << "\n";
    currentValue = "%extra";
    return;
  }
}

void emitStraightLine(std::ostream &out, const GeneratorConfig &config) {
  std::string currentValue = "%x";
  out << arithmeticInstruction("%x", arithOpcode(config), "%a", rhsValue(config),
                               config);
  emitExtra(out, config, currentValue);
  out << "  ret i32 " << currentValue << "\n";
}

void emitDiamond(std::ostream &out, const GeneratorConfig &config) {
  out << "  %cmp = icmp " << toString(predicate(config)) << " i32 %a, %b\n";
  if (useSelect(config)) {
    // Branchless select form: both arms are computed unconditionally so the
    // select operands dominate their use. (A branching diamond whose arms feed
    // a select in the merge block produces invalid IR.)
    out << arithmeticInstruction("%then.v", arithOpcode(config), "%a",
                                 rhsValue(config), config);
    out << arithmeticInstruction("%else.v", arithOpcode(config), "%b",
                                 std::to_string(config.constB), config);
    out << "  %merged = select i1 %cmp, i32 %then.v, i32 %else.v\n";
    std::string currentValue = "%merged";
    emitExtra(out, config, currentValue);
    out << "  ret i32 " << currentValue << "\n";
    return;
  }
  out << "  br i1 %cmp, label %then, label %else\n\n";
  out << "then:\n";
  out << arithmeticInstruction("%then.v", arithOpcode(config), "%a",
                               rhsValue(config), config);
  out << "  br label %merge\n\n";
  out << "else:\n";
  out << arithmeticInstruction("%else.v", arithOpcode(config), "%b",
                               std::to_string(config.constB), config);
  out << "  br label %merge\n\n";
  out << "merge:\n";
  out << "  %merged = phi i32 [ %then.v, %then ], [ %else.v, %else ]\n";
  std::string currentValue = "%merged";
  emitExtra(out, config, currentValue);
  out << "  ret i32 " << currentValue << "\n";
}

void emitNestedDiamond(std::ostream &out, const GeneratorConfig &config) {
  out << "  %cmp = icmp " << toString(predicate(config)) << " i32 %a, %b\n";
  out << "  br i1 %cmp, label %then.outer, label %else.outer\n\n";

  out << "then.outer:\n";
  out << "  %cmp.inner = icmp ne i32 %a, " << config.constA << "\n";
  out << "  br i1 %cmp.inner, label %then.inner, label %else.inner\n\n";

  out << "then.inner:\n";
  out << arithmeticInstruction("%then.inner.v", arithOpcode(config), "%a",
                               rhsValue(config), config);
  out << "  br label %merge\n\n";

  out << "else.inner:\n";
  out << arithmeticInstruction("%else.inner.v", arithOpcode(config), "%b",
                               std::to_string(config.constA), config);
  out << "  br label %merge\n\n";

  out << "else.outer:\n";
  out << arithmeticInstruction("%else.outer.v", arithOpcode(config), "%b",
                               std::to_string(config.constB), config);
  out << "  br label %merge\n\n";

  out << "merge:\n";
  out << "  %merged = phi i32 [ %then.inner.v, %then.inner ], "
      << "[ %else.inner.v, %else.inner ], [ %else.outer.v, %else.outer ]\n";
  std::string currentValue = "%merged";
  emitExtra(out, config, currentValue);
  out << "  ret i32 " << currentValue << "\n";
}

void emitUnreachableTail(std::ostream &out, const GeneratorConfig &config) {
  std::string currentValue = "%x";
  out << arithmeticInstruction("%x", arithOpcode(config), "%a", rhsValue(config),
                               config);
  emitExtra(out, config, currentValue);
  out << "  ret i32 " << currentValue << "\n\n";

  out << "unreachable.tail:\n";
  out << "  %unreachable.dead = add i32 %b, " << config.constB << "\n";
  out << "  unreachable\n";
}

void emitSwitchLikeChain(std::ostream &out, const GeneratorConfig &config) {
  out << "  %is.a = icmp eq i32 %a, " << config.constA << "\n";
  out << "  br i1 %is.a, label %case.a, label %check.b\n\n";

  out << "case.a:\n";
  out << arithmeticInstruction("%case.a.v", arithOpcode(config), "%a",
                               rhsValue(config), config);
  out << "  br label %merge\n\n";

  out << "check.b:\n";
  out << "  %is.b = icmp eq i32 %b, " << config.constB << "\n";
  out << "  br i1 %is.b, label %case.b, label %default\n\n";

  out << "case.b:\n";
  out << arithmeticInstruction("%case.b.v", arithOpcode(config), "%b", "1",
                               config);
  out << "  br label %merge\n\n";

  out << "default:\n";
  out << arithmeticInstruction("%default.v", arithOpcode(config), "%a", "%b",
                               config);
  out << "  br label %merge\n\n";

  out << "merge:\n";
  out << "  %merged = phi i32 [ %case.a.v, %case.a ], "
      << "[ %case.b.v, %case.b ], [ %default.v, %default ]\n";
  std::string currentValue = "%merged";
  emitExtra(out, config, currentValue);
  out << "  ret i32 " << currentValue << "\n";
}

void emitVectorBase(std::ostream &out) {
  out << "  %v0 = insertelement <4 x i32> poison, i32 %a, i32 0\n";
  out << "  %v1 = insertelement <4 x i32> %v0, i32 %b, i32 1\n";
  out << "  %v2 = insertelement <4 x i32> %v1, i32 %a, i32 2\n";
  out << "  %vec = insertelement <4 x i32> %v2, i32 %b, i32 3\n";
}

void emitScalableVectorBase(std::ostream &out) {
  out << "  %svec = insertelement <vscale x 4 x i32> poison, i32 %a, i32 0\n";
}

void emitVector(std::ostream &out, const GeneratorConfig &config) {
  const VectorShape vecShape = vectorShape(config);
  if (vecShape == VectorShape::ScalableAddZero ||
      vecShape == VectorShape::ScalableMulOne ||
      vecShape == VectorShape::ScalableXorSelf ||
      vecShape == VectorShape::ScalableSubZero ||
      vecShape == VectorShape::ScalableOrZero ||
      vecShape == VectorShape::ScalableAndAllOnes ||
      vecShape == VectorShape::ScalableReductionAddZero) {
    emitScalableVectorBase(out);
    switch (vecShape) {
    case VectorShape::ScalableAddZero:
      out << "  %result.svec = add <vscale x 4 x i32> %svec, zeroinitializer\n";
      break;
    case VectorShape::ScalableMulOne:
      out << "  %ones = insertelement <vscale x 4 x i32> poison, i32 1, i32 0\n";
      out << "  %result.svec = mul <vscale x 4 x i32> %svec, %ones\n";
      break;
    case VectorShape::ScalableXorSelf:
      out << "  %result.svec = xor <vscale x 4 x i32> %svec, %svec\n";
      break;
    case VectorShape::ScalableSubZero:
      out << "  %result.svec = sub <vscale x 4 x i32> %svec, zeroinitializer\n";
      break;
    case VectorShape::ScalableOrZero:
      out << "  %result.svec = or <vscale x 4 x i32> %svec, zeroinitializer\n";
      break;
    case VectorShape::ScalableAndAllOnes:
      out << "  %ones = insertelement <vscale x 4 x i32> poison, i32 -1, i32 0\n";
      out << "  %result.svec = and <vscale x 4 x i32> %svec, %ones\n";
      break;
    case VectorShape::ScalableReductionAddZero:
      out << "  %result = call i32 @llvm.vector.reduce.add.nxv4i32(<vscale x 4 x i32> zeroinitializer)\n";
      out << "  ret i32 %result\n";
      return;
    default:
      break;
    }
    out << "  %result = extractelement <vscale x 4 x i32> %result.svec, i32 0\n";
    out << "  ret i32 %result\n";
    return;
  }
  emitVectorBase(out);
  switch (vecShape) {
  case VectorShape::None:
    out << "  ret i32 %a\n";
    return;
  case VectorShape::AddZero:
    out << "  %result.vec = add <4 x i32> %vec, zeroinitializer\n";
    break;
  case VectorShape::MulOne:
    out << "  %result.vec = mul <4 x i32> %vec, <i32 1, i32 1, i32 1, i32 1>\n";
    break;
  case VectorShape::XorSelf:
    out << "  %result.vec = xor <4 x i32> %vec, %vec\n";
    break;
  case VectorShape::ShuffleIdentity:
    out << "  %result.vec = shufflevector <4 x i32> %vec, <4 x i32> poison, <4 x i32> <i32 0, i32 1, i32 2, i32 3>\n";
    break;
  case VectorShape::ShuffleSplat:
    out << "  %result.vec = shufflevector <4 x i32> %vec, <4 x i32> poison, <4 x i32> <i32 2, i32 2, i32 2, i32 2>\n";
    break;
  case VectorShape::ExtractInsert:
    out << "  %inserted = insertelement <4 x i32> %vec, i32 " << config.constA
        << ", i32 1\n";
    out << "  %result = extractelement <4 x i32> %inserted, i32 1\n";
    out << "  ret i32 %result\n";
    return;
  case VectorShape::ReductionAddZero:
    out << "  %result = call i32 @llvm.vector.reduce.add.v4i32(<4 x i32> zeroinitializer)\n";
    out << "  ret i32 %result\n";
    return;
  case VectorShape::SubZero:
    out << "  %result.vec = sub <4 x i32> %vec, zeroinitializer\n";
    break;
  case VectorShape::OrZero:
    out << "  %result.vec = or <4 x i32> %vec, zeroinitializer\n";
    break;
  case VectorShape::AndAllOnes:
    out << "  %result.vec = and <4 x i32> %vec, <i32 -1, i32 -1, i32 -1, i32 -1>\n";
    break;
  case VectorShape::InsertExtractIdentity:
    out << "  %lane = extractelement <4 x i32> %vec, i32 1\n";
    out << "  %result.vec = insertelement <4 x i32> %vec, i32 %lane, i32 1\n";
    break;
  case VectorShape::ReductionAddSingleLane:
    out << "  %single0 = insertelement <4 x i32> zeroinitializer, i32 %a, i32 0\n";
    out << "  %result = call i32 @llvm.vector.reduce.add.v4i32(<4 x i32> %single0)\n";
    out << "  ret i32 %result\n";
    return;
  case VectorShape::SMin:
    out << "  %rhs.vec = shufflevector <4 x i32> %vec, <4 x i32> poison, <4 x i32> <i32 1, i32 0, i32 3, i32 2>\n";
    out << "  %cmp = icmp slt <4 x i32> %vec, %rhs.vec\n";
    out << "  %result.vec = select <4 x i1> %cmp, <4 x i32> %vec, <4 x i32> %rhs.vec\n";
    break;
  case VectorShape::SMax:
    out << "  %rhs.vec = shufflevector <4 x i32> %vec, <4 x i32> poison, <4 x i32> <i32 1, i32 0, i32 3, i32 2>\n";
    out << "  %cmp = icmp sgt <4 x i32> %vec, %rhs.vec\n";
    out << "  %result.vec = select <4 x i1> %cmp, <4 x i32> %vec, <4 x i32> %rhs.vec\n";
    break;
  case VectorShape::UMin:
    out << "  %rhs.vec = shufflevector <4 x i32> %vec, <4 x i32> poison, <4 x i32> <i32 1, i32 0, i32 3, i32 2>\n";
    out << "  %cmp = icmp ult <4 x i32> %vec, %rhs.vec\n";
    out << "  %result.vec = select <4 x i1> %cmp, <4 x i32> %vec, <4 x i32> %rhs.vec\n";
    break;
  case VectorShape::UMax:
    out << "  %rhs.vec = shufflevector <4 x i32> %vec, <4 x i32> poison, <4 x i32> <i32 1, i32 0, i32 3, i32 2>\n";
    out << "  %cmp = icmp ugt <4 x i32> %vec, %rhs.vec\n";
    out << "  %result.vec = select <4 x i1> %cmp, <4 x i32> %vec, <4 x i32> %rhs.vec\n";
    break;
  case VectorShape::Abs:
    out << "  %neg = sub <4 x i32> zeroinitializer, %vec\n";
    out << "  %cmp = icmp slt <4 x i32> %vec, zeroinitializer\n";
    out << "  %result.vec = select <4 x i1> %cmp, <4 x i32> %neg, <4 x i32> %vec\n";
    break;
  case VectorShape::ScalableAddZero:
  case VectorShape::ScalableMulOne:
  case VectorShape::ScalableXorSelf:
  case VectorShape::ScalableSubZero:
  case VectorShape::ScalableOrZero:
  case VectorShape::ScalableAndAllOnes:
  case VectorShape::ScalableReductionAddZero:
    return;
  }
  out << "  %result = extractelement <4 x i32> %result.vec, i32 0\n";
  out << "  ret i32 %result\n";
}

void emitGlobalDeadInitializer(std::ostream &out, const GeneratorConfig &config) {
  out << "; marker=probe.globalopt.dead-initializer\n";
  out << "; witness_model=global-initializer-default-null-family-v1\n\n";
  switch (globalShape(config)) {
  case GlobalShape::None:
    return;
  case GlobalShape::DeadInitializerI32:
    out << "@cv_dead_init = internal global i32 42\n\n";
    break;
  case GlobalShape::DeadInitializerPtr:
    out << "@cv_target = internal global i32 7\n";
    out << "@cv_dead_init = internal global ptr @cv_target\n\n";
    break;
  case GlobalShape::DeadInitializerArray:
    out << "@cv_dead_init = internal global [2 x i32] [i32 1, i32 2]\n\n";
    break;
  }
  out << "define i32 @cv_observe(i32 %x) {\n";
  out << "entry:\n";
  out << "  ret i32 %x\n";
  out << "}\n";
}

void emitMetadata(std::ostream &out, const GeneratorConfig &config) {
  out << "; arith_opcode=" << static_cast<unsigned>(config.arithOpcode)
      << " (" << toString(arithOpcode(config)) << ")\n";
  out << "; rhs_mode=" << static_cast<unsigned>(config.rhsMode)
      << " (" << toString(rhsMode(config)) << ")\n";
  out << "; extra_opcode=" << static_cast<unsigned>(config.extraOpcode)
      << " (" << toString(extraOpcode(config)) << ")\n";
  out << "; predicate=" << static_cast<unsigned>(config.predicate)
      << " (" << toString(predicate(config)) << ")\n";
  out << "; shape=" << static_cast<unsigned>(config.shape)
      << " (" << toString(shape(config)) << ")\n";
  out << "; memory_shape=" << static_cast<unsigned>(config.memoryShape)
      << " (" << toString(memoryShape(config)) << ")\n";
  out << "; pointer_mode=" << static_cast<unsigned>(config.pointerMode)
      << " (" << toString(pointerMode(config)) << ")\n";
  out << "; store_mode=" << static_cast<unsigned>(config.storeMode)
      << " (" << toString(storeMode(config)) << ")\n";
  out << "; load_use_mode=" << static_cast<unsigned>(config.loadUseMode)
      << " (" << toString(loadUseMode(config)) << ")\n";
  out << "; loop_shape=" << static_cast<unsigned>(config.loopShape)
      << " (" << toString(loopShape(config)) << ")\n";
  out << "; loop_trip_mode=" << static_cast<unsigned>(config.loopTripMode)
      << " (" << toString(loopTripMode(config)) << ")\n";
  out << "; induction_mode=" << static_cast<unsigned>(config.inductionMode)
      << " (" << toString(inductionMode(config)) << ")\n";
  out << "; loop_use_mode=" << static_cast<unsigned>(config.loopUseMode)
      << " (" << toString(loopUseMode(config)) << ")\n";
  out << "; vector_shape=" << static_cast<unsigned>(config.vectorShape)
      << " (" << toString(vectorShape(config)) << ")\n";
  out << "; global_shape=" << static_cast<unsigned>(config.globalShape)
      << " (" << toString(globalShape(config)) << ")\n";
  out << "; compose_bits=" << static_cast<unsigned>(config.composeBits) << "\n";
  out << "; int_width=" << static_cast<unsigned>(config.intWidth) << " ("
      << toString(intWidth(config)) << ")\n";
  out << "; scalar_args=" << static_cast<unsigned>(config.scalarArgs) << " ("
      << scalarArgCount(config) << ")\n";
  out << "; pointer_args=" << static_cast<unsigned>(config.pointerArgs) << "\n";
  out << "; pointer_noalias=" << static_cast<unsigned>(config.pointerNoalias)
      << "\n";
  out << "; cast_mode=" << static_cast<unsigned>(config.castMode) << "\n";
}

// --- Composable shape stages -------------------------------------------------
//
// Each stage emits a single-entry/single-exit region that threads a running
// value, takes the label of the block control currently sits in, and returns
// the new running value plus the block control now sits in (with no terminator
// emitted yet). All names and labels are prefixed per-stage so multiple regions
// can co-exist in one function. The driver (`generateComposed`) sequences the
// enabled stages and emits the single trailing `ret`.

struct StageOut {
  std::string value;
  std::string block;
};

StageOut composeCfgStage(std::ostream &out, const GeneratorConfig &config,
                         const std::string &p, const std::string &input,
                         const std::string &entryBlock, const std::string &ty) {
  const std::string rhs = rhsValue(config);
  const ArithOpcode op = arithOpcode(config);
  const bool nsw = hasNsw(config);
  const bool nuw = hasNuw(config);
  switch (shape(config)) {
  case Shape::StraightLine:
  case Shape::UnreachableTail: {
    // Unreachable-tail folds to straight-line in composed mode (a danging
    // unreachable block has no clean SESE exit to thread through).
    const std::string v = "%" + p + ".x";
    out << arithmeticInstruction(v.c_str(), op, input, rhs, nsw, nuw, ty);
    return {v, entryBlock};
  }
  case Shape::Diamond: {
    out << "  %" << p << ".cmp = icmp " << toString(predicate(config)) << " "
        << ty << " %a, %b\n";
    const std::string tv = "%" + p + ".then.v";
    const std::string ev = "%" + p + ".else.v";
    const std::string mv = "%" + p + ".merged";
    if (useSelect(config)) {
      // Branchless select form keeps the region in the current block so both
      // operands dominate the select (see emitDiamond for the legacy rationale).
      out << arithmeticInstruction(tv.c_str(), op, input, rhs, nsw, nuw, ty);
      out << arithmeticInstruction(ev.c_str(), op, "%b",
                                   std::to_string(config.constB), nsw, nuw, ty);
      out << "  " << mv << " = select i1 %" << p << ".cmp, " << ty << " " << tv
          << ", " << ty << " " << ev << "\n";
      return {mv, entryBlock};
    }
    out << "  br i1 %" << p << ".cmp, label %" << p << ".then, label %" << p
        << ".else\n\n";
    out << p << ".then:\n";
    out << arithmeticInstruction(tv.c_str(), op, input, rhs, nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".else:\n";
    out << arithmeticInstruction(ev.c_str(), op, "%b",
                                 std::to_string(config.constB), nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".merge:\n";
    out << "  " << mv << " = phi " << ty << " [ " << tv << ", %" << p
        << ".then ], [ " << ev << ", %" << p << ".else ]\n";
    return {mv, p + ".merge"};
  }
  case Shape::NestedDiamond: {
    out << "  %" << p << ".cmp = icmp " << toString(predicate(config)) << " "
        << ty << " %a, %b\n";
    out << "  br i1 %" << p << ".cmp, label %" << p << ".then.outer, label %"
        << p << ".else.outer\n\n";
    out << p << ".then.outer:\n";
    out << "  %" << p << ".cmp.inner = icmp ne " << ty << " %a, " << config.constA
        << "\n";
    out << "  br i1 %" << p << ".cmp.inner, label %" << p
        << ".then.inner, label %" << p << ".else.inner\n\n";
    out << p << ".then.inner:\n";
    const std::string tiv = "%" + p + ".then.inner.v";
    out << arithmeticInstruction(tiv.c_str(), op, input, rhs, nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".else.inner:\n";
    const std::string eiv = "%" + p + ".else.inner.v";
    out << arithmeticInstruction(eiv.c_str(), op, "%b",
                                 std::to_string(config.constA), nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".else.outer:\n";
    const std::string eov = "%" + p + ".else.outer.v";
    out << arithmeticInstruction(eov.c_str(), op, "%b",
                                 std::to_string(config.constB), nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".merge:\n";
    const std::string mv = "%" + p + ".merged";
    out << "  " << mv << " = phi " << ty << " [ " << tiv << ", %" << p
        << ".then.inner ], [ " << eiv << ", %" << p << ".else.inner ], [ "
        << eov << ", %" << p << ".else.outer ]\n";
    return {mv, p + ".merge"};
  }
  case Shape::SwitchLikeChain: {
    out << "  %" << p << ".is.a = icmp eq " << ty << " %a, " << config.constA
        << "\n";
    out << "  br i1 %" << p << ".is.a, label %" << p << ".case.a, label %" << p
        << ".check.b\n\n";
    out << p << ".case.a:\n";
    const std::string av = "%" + p + ".case.a.v";
    out << arithmeticInstruction(av.c_str(), op, input, rhs, nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".check.b:\n";
    out << "  %" << p << ".is.b = icmp eq " << ty << " %b, " << config.constB
        << "\n";
    out << "  br i1 %" << p << ".is.b, label %" << p << ".case.b, label %" << p
        << ".default\n\n";
    out << p << ".case.b:\n";
    const std::string bv = "%" + p + ".case.b.v";
    out << arithmeticInstruction(bv.c_str(), op, "%b", "1", nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".default:\n";
    const std::string dv = "%" + p + ".default.v";
    out << arithmeticInstruction(dv.c_str(), op, input, "%b", nsw, nuw, ty);
    out << "  br label %" << p << ".merge\n\n";
    out << p << ".merge:\n";
    const std::string mv = "%" + p + ".merged";
    out << "  " << mv << " = phi " << ty << " [ " << av << ", %" << p
        << ".case.a ], [ " << bv << ", %" << p << ".case.b ], [ " << dv << ", %"
        << p << ".default ]\n";
    return {mv, p + ".merge"};
  }
  }
  return {input, entryBlock};
}

StageOut composeMemoryStage(std::ostream &out, const GeneratorConfig &config,
                            const std::string &p, const std::string &input,
                            const std::string &entryBlock, const std::string &ty) {
  // With pointer parameters, route stores/loads through them instead of a local
  // alloca. The two-pointer patterns interpose a store to %q between a store and
  // load on %p: with `noalias` the load forwards `input` (DSE/GVN fire); with
  // may-alias it cannot. That `noalias`-vs-may-alias gap is the alias-reasoning
  // trigger that local allocas can never express.
  if (config.pointerArgs >= 1U) {
    const std::string p0 = pointerArgName(0);
    const bool twoPtr = config.pointerArgs >= 2U;
    const std::string p1 = twoPtr ? pointerArgName(1) : p0;
    switch (memoryShape(config)) {
    case MemoryShape::None:
      return {input, entryBlock};
    case MemoryShape::AllocaStoreLoad: {
      out << "  store " << ty << " " << input << ", ptr " << p0 << ", align 4\n";
      if (twoPtr) {
        out << "  store " << ty << " " << config.constB << ", ptr " << p1
            << ", align 4\n";
      }
      const std::string lv = "%" + p + ".loaded";
      out << "  " << lv << " = load " << ty << ", ptr " << p0 << ", align 4\n";
      return {lv, entryBlock};
    }
    case MemoryShape::LoadAfterStore: {
      out << "  store " << ty << " " << input << ", ptr " << p0 << ", align 4\n";
      const std::string l1 = "%" + p + ".loaded";
      out << "  " << l1 << " = load " << ty << ", ptr " << p0 << ", align 4\n";
      if (twoPtr) {
        out << "  store " << ty << " " << config.constB << ", ptr " << p1
            << ", align 4\n";
      }
      const std::string l2 = "%" + p + ".loaded.again";
      out << "  " << l2 << " = load " << ty << ", ptr " << p0 << ", align 4\n";
      return {l2, entryBlock};
    }
    case MemoryShape::DeadStore:
      out << "  store " << ty << " " << config.constA << ", ptr " << p0
          << ", align 4\n";
      return {input, entryBlock};
    case MemoryShape::OverwrittenStore: {
      out << "  store " << ty << " " << config.constA << ", ptr " << p0
          << ", align 4\n";
      out << "  store " << ty << " " << input << ", ptr " << p0 << ", align 4\n";
      const std::string lv = "%" + p + ".loaded";
      out << "  " << lv << " = load " << ty << ", ptr " << p0 << ", align 4\n";
      return {lv, entryBlock};
    }
    case MemoryShape::UnusedAlloca:
      return {input, entryBlock};
    }
    return {input, entryBlock};
  }

  // The conditional-store and multi-slot pointer variants introduce extra
  // blocks; the composed memory stage keeps a single direct slot for now.
  // `align 4` is valid for any width (alignment need not match the type size).
  const std::string slot = "%" + p + ".slot";
  out << "  " << slot << " = alloca " << ty << ", align 4\n";
  switch (memoryShape(config)) {
  case MemoryShape::None:
    return {input, entryBlock};
  case MemoryShape::AllocaStoreLoad: {
    out << "  store " << ty << " " << input << ", ptr " << slot << ", align 4\n";
    if (storeMode(config) == StoreMode::DoubleStore) {
      out << "  store " << ty << " " << config.constB << ", ptr " << slot
          << ", align 4\n";
    }
    const std::string lv = "%" + p + ".loaded";
    out << "  " << lv << " = load " << ty << ", ptr " << slot << ", align 4\n";
    return {lv, entryBlock};
  }
  case MemoryShape::LoadAfterStore: {
    out << "  store " << ty << " " << input << ", ptr " << slot << ", align 4\n";
    const std::string l1 = "%" + p + ".loaded";
    const std::string l2 = "%" + p + ".loaded.again";
    out << "  " << l1 << " = load " << ty << ", ptr " << slot << ", align 4\n";
    out << "  " << l2 << " = load " << ty << ", ptr " << slot << ", align 4\n";
    return {l2, entryBlock};
  }
  case MemoryShape::DeadStore:
    out << "  store " << ty << " " << config.constA << ", ptr " << slot
        << ", align 4\n";
    return {input, entryBlock};
  case MemoryShape::OverwrittenStore: {
    out << "  store " << ty << " " << config.constA << ", ptr " << slot
        << ", align 4\n";
    out << "  store " << ty << " " << input << ", ptr " << slot << ", align 4\n";
    const std::string lv = "%" + p + ".loaded";
    out << "  " << lv << " = load " << ty << ", ptr " << slot << ", align 4\n";
    return {lv, entryBlock};
  }
  case MemoryShape::UnusedAlloca:
    return {input, entryBlock};
  }
  return {input, entryBlock};
}

StageOut composeLoopStage(std::ostream &out, const GeneratorConfig &config,
                          const std::string &p, const std::string &input,
                          const std::string &entryBlock, const std::string &ty) {
  std::string limit;
  switch (loopTripMode(config)) {
  case LoopTripMode::ConstantSmall:
    limit = std::to_string(positiveSmall(config.constA, 4, 8));
    break;
  case LoopTripMode::ArgumentBounded:
    out << "  %" << p << ".limit.mask = and " << ty << " %b, 7\n";
    out << "  %" << p << ".limit = add " << ty << " %" << p << ".limit.mask, 1\n";
    limit = "%" + p + ".limit";
    break;
  case LoopTripMode::SingleIteration:
    limit = "1";
    break;
  }
  const std::string start = inductionStart(config, limit);
  const std::string i = "%" + p + ".i";
  const std::string acc = "%" + p + ".acc";
  const std::string next = "%" + p + ".next";
  const std::string accNext = "%" + p + ".acc.next";

  out << "  br label %" << p << ".loop.header\n\n";
  out << p << ".loop.header:\n";
  out << "  " << i << " = phi " << ty << " [ " << start << ", %" << entryBlock
      << " ], [ " << next << ", %" << p << ".loop.latch ]\n";
  out << "  " << acc << " = phi " << ty << " [ " << input << ", %" << entryBlock
      << " ], [ " << accNext << ", %" << p << ".loop.latch ]\n";
  out << "  %" << p << ".cond = icmp " << inductionPredicate(config) << " " << ty
      << " " << i << ", " << inductionCompareRhs(config, limit) << "\n";
  out << "  br i1 %" << p << ".cond, label %" << p << ".loop.body, label %" << p
      << ".loop.exit\n\n";

  out << p << ".loop.body:\n";
  const std::string bodyV = "%" + p + ".body.v";
  if (loopShape(config) == LoopShape::InvariantOpLoop) {
    out << "  %" << p << ".invariant = add " << ty << " %a, " << config.constB
        << "\n";
    out << "  " << bodyV << " = add " << ty << " %" << p << ".invariant, 1\n";
  } else {
    out << arithmeticInstruction(bodyV.c_str(), arithOpcode(config), i,
                                 rhsValue(config), hasNsw(config), hasNuw(config),
                                 ty);
  }
  if (loopShape(config) == LoopShape::DeadBodyLoop) {
    out << "  %" << p << ".loop.dead = add " << ty << " %a, " << config.constB
        << "\n";
  }
  if (loopShape(config) == LoopShape::EarlyExitLoop) {
    out << "  %" << p << ".early.exit = icmp eq " << ty << " " << bodyV << ", "
        << config.constA << "\n";
    out << "  br i1 %" << p << ".early.exit, label %" << p
        << ".loop.exit, label %" << p << ".loop.latch\n\n";
  } else {
    out << "  br label %" << p << ".loop.latch\n\n";
  }

  out << p << ".loop.latch:\n";
  out << "  " << next << " = add " << ty << " " << i << ", "
      << inductionStep(config) << "\n";
  out << "  " << accNext << " = add " << ty << " " << acc << ", " << bodyV
      << "\n";
  out << "  br label %" << p << ".loop.header\n\n";

  out << p << ".loop.exit:\n";
  return {acc, p + ".loop.exit"};
}

bool isScalableVectorShape(VectorShape shape) {
  switch (shape) {
  case VectorShape::ScalableAddZero:
  case VectorShape::ScalableMulOne:
  case VectorShape::ScalableXorSelf:
  case VectorShape::ScalableSubZero:
  case VectorShape::ScalableOrZero:
  case VectorShape::ScalableAndAllOnes:
  case VectorShape::ScalableReductionAddZero:
    return true;
  default:
    return false;
  }
}

// Module-level declare a composed vector stage needs, or "" if none.
std::string composedVectorDeclare(const GeneratorConfig &config) {
  const std::string ty = intTy(config);
  switch (vectorShape(config)) {
  case VectorShape::ReductionAddZero:
  case VectorShape::ReductionAddSingleLane:
    return "declare " + ty + " @llvm.vector.reduce.add.v4" + ty + "(<4 x " + ty +
           ">)\n";
  case VectorShape::ScalableReductionAddZero:
    return "declare " + ty + " @llvm.vector.reduce.add.nxv4" + ty +
           "(<vscale x 4 x " + ty + ">)\n";
  default:
    return "";
  }
}

// Vector stage: lift the running scalar into a vector, apply the identity-style
// op, and extract a lane back to a scalar so the value keeps threading.
StageOut composeVectorStage(std::ostream &out, const GeneratorConfig &config,
                            const std::string &p, const std::string &input,
                            const std::string &entryBlock, const std::string &ty) {
  const VectorShape vecShape = vectorShape(config);
  const std::string result = "%" + p + ".result";
  // Element type follows `ty`; lane indices and shuffle masks are always i32.
  const std::string vty = "<4 x " + ty + ">";
  const std::string svty = "<vscale x 4 x " + ty + ">";
  const std::string ones = "<" + ty + " 1, " + ty + " 1, " + ty + " 1, " + ty +
                           " 1>";
  const std::string allOnes = "<" + ty + " -1, " + ty + " -1, " + ty + " -1, " +
                              ty + " -1>";

  if (isScalableVectorShape(vecShape)) {
    const std::string svec = "%" + p + ".svec";
    out << "  " << svec << " = insertelement " << svty << " poison, " << ty << " "
        << input << ", i32 0\n";
    const std::string rvec = "%" + p + ".result.svec";
    switch (vecShape) {
    case VectorShape::ScalableAddZero:
      out << "  " << rvec << " = add " << svty << " " << svec
          << ", zeroinitializer\n";
      break;
    case VectorShape::ScalableMulOne:
      out << "  %" << p << ".ones = insertelement " << svty << " poison, " << ty
          << " 1, i32 0\n";
      out << "  " << rvec << " = mul " << svty << " " << svec << ", %" << p
          << ".ones\n";
      break;
    case VectorShape::ScalableXorSelf:
      out << "  " << rvec << " = xor " << svty << " " << svec << ", " << svec
          << "\n";
      break;
    case VectorShape::ScalableSubZero:
      out << "  " << rvec << " = sub " << svty << " " << svec
          << ", zeroinitializer\n";
      break;
    case VectorShape::ScalableOrZero:
      out << "  " << rvec << " = or " << svty << " " << svec
          << ", zeroinitializer\n";
      break;
    case VectorShape::ScalableAndAllOnes:
      out << "  %" << p << ".ones = insertelement " << svty << " poison, " << ty
          << " -1, i32 0\n";
      out << "  " << rvec << " = and " << svty << " " << svec << ", %" << p
          << ".ones\n";
      break;
    case VectorShape::ScalableReductionAddZero:
      out << "  " << result << " = call " << ty
          << " @llvm.vector.reduce.add.nxv4" << ty << "(" << svty
          << " zeroinitializer)\n";
      return {result, entryBlock};
    default:
      break;
    }
    out << "  " << result << " = extractelement " << svty << " " << rvec
        << ", i32 0\n";
    return {result, entryBlock};
  }

  const std::string vec = "%" + p + ".vec";
  out << "  %" << p << ".v0 = insertelement " << vty << " poison, " << ty << " "
      << input << ", i32 0\n";
  out << "  %" << p << ".v1 = insertelement " << vty << " %" << p << ".v0, " << ty
      << " %b, i32 1\n";
  out << "  %" << p << ".v2 = insertelement " << vty << " %" << p << ".v1, " << ty
      << " " << input << ", i32 2\n";
  out << "  " << vec << " = insertelement " << vty << " %" << p << ".v2, " << ty
      << " %b, i32 3\n";
  const std::string rvec = "%" + p + ".result.vec";
  const std::string cmp = "%" + p + ".cmp";
  const std::string rhs = "%" + p + ".rhs.vec";
  switch (vecShape) {
  case VectorShape::None:
    return {input, entryBlock};
  case VectorShape::AddZero:
    out << "  " << rvec << " = add " << vty << " " << vec << ", zeroinitializer\n";
    break;
  case VectorShape::MulOne:
    out << "  " << rvec << " = mul " << vty << " " << vec << ", " << ones << "\n";
    break;
  case VectorShape::XorSelf:
    out << "  " << rvec << " = xor " << vty << " " << vec << ", " << vec << "\n";
    break;
  case VectorShape::ShuffleIdentity:
    out << "  " << rvec << " = shufflevector " << vty << " " << vec << ", " << vty
        << " poison, <4 x i32> <i32 0, i32 1, i32 2, i32 3>\n";
    break;
  case VectorShape::ShuffleSplat:
    out << "  " << rvec << " = shufflevector " << vty << " " << vec << ", " << vty
        << " poison, <4 x i32> <i32 2, i32 2, i32 2, i32 2>\n";
    break;
  case VectorShape::ExtractInsert:
    out << "  %" << p << ".inserted = insertelement " << vty << " " << vec << ", "
        << ty << " " << config.constA << ", i32 1\n";
    out << "  " << result << " = extractelement " << vty << " %" << p
        << ".inserted, i32 1\n";
    return {result, entryBlock};
  case VectorShape::ReductionAddZero:
    out << "  " << result << " = call " << ty << " @llvm.vector.reduce.add.v4"
        << ty << "(" << vty << " zeroinitializer)\n";
    return {result, entryBlock};
  case VectorShape::SubZero:
    out << "  " << rvec << " = sub " << vty << " " << vec << ", zeroinitializer\n";
    break;
  case VectorShape::OrZero:
    out << "  " << rvec << " = or " << vty << " " << vec << ", zeroinitializer\n";
    break;
  case VectorShape::AndAllOnes:
    out << "  " << rvec << " = and " << vty << " " << vec << ", " << allOnes
        << "\n";
    break;
  case VectorShape::InsertExtractIdentity:
    out << "  %" << p << ".lane = extractelement " << vty << " " << vec
        << ", i32 1\n";
    out << "  " << rvec << " = insertelement " << vty << " " << vec << ", " << ty
        << " %" << p << ".lane, i32 1\n";
    break;
  case VectorShape::ReductionAddSingleLane:
    out << "  %" << p << ".single0 = insertelement " << vty
        << " zeroinitializer, " << ty << " " << input << ", i32 0\n";
    out << "  " << result << " = call " << ty << " @llvm.vector.reduce.add.v4"
        << ty << "(" << vty << " %" << p << ".single0)\n";
    return {result, entryBlock};
  case VectorShape::SMin:
  case VectorShape::SMax:
  case VectorShape::UMin:
  case VectorShape::UMax: {
    const char *pred = vecShape == VectorShape::SMin   ? "slt"
                       : vecShape == VectorShape::SMax ? "sgt"
                       : vecShape == VectorShape::UMin ? "ult"
                                                       : "ugt";
    out << "  " << rhs << " = shufflevector " << vty << " " << vec << ", " << vty
        << " poison, <4 x i32> <i32 1, i32 0, i32 3, i32 2>\n";
    out << "  " << cmp << " = icmp " << pred << " " << vty << " " << vec << ", "
        << rhs << "\n";
    out << "  " << rvec << " = select <4 x i1> " << cmp << ", " << vty << " "
        << vec << ", " << vty << " " << rhs << "\n";
    break;
  }
  case VectorShape::Abs:
    out << "  %" << p << ".neg = sub " << vty << " zeroinitializer, " << vec
        << "\n";
    out << "  " << cmp << " = icmp slt " << vty << " " << vec
        << ", zeroinitializer\n";
    out << "  " << rvec << " = select <4 x i1> " << cmp << ", " << vty << " %" << p
        << ".neg, " << vty << " " << vec << "\n";
    break;
  case VectorShape::ScalableAddZero:
  case VectorShape::ScalableMulOne:
  case VectorShape::ScalableXorSelf:
  case VectorShape::ScalableSubZero:
  case VectorShape::ScalableOrZero:
  case VectorShape::ScalableAndAllOnes:
  case VectorShape::ScalableReductionAddZero:
    return {input, entryBlock};  // handled above
  }
  out << "  " << result << " = extractelement " << vty << " " << rvec
      << ", i32 0\n";
  return {result, entryBlock};
}

unsigned widthBitsOf(IntWidth w) {
  switch (w) {
  case IntWidth::I8:
    return 8;
  case IntWidth::I16:
    return 16;
  case IntWidth::I32:
    return 32;
  case IntWidth::I64:
    return 64;
  }
  return 32;
}

// Cast stage: round-trip the running scalar through a different width with a
// trunc/zext-or-sext pair (plus a non-identity add in the intermediate width),
// so InstCombine's cast and known-bits folds have something to chew on. Threads
// the value in and out at `ty`.
StageOut composeCastStage(std::ostream &out, const GeneratorConfig &config,
                          const std::string &p, const std::string &input,
                          const std::string &entryBlock, const std::string &ty) {
  const IntWidth from = intWidth(config);
  unsigned targetIdx = config.castMode & 3U;
  if (targetIdx == static_cast<unsigned>(from)) {
    targetIdx = (targetIdx + 1U) & 3U;  // ensure a different width
  }
  const IntWidth to = static_cast<IntWidth>(targetIdx);
  const std::string sty = toString(to);
  const char *widen = (config.castMode & 4U) != 0U ? "sext" : "zext";
  const std::string mid = "%" + p + ".cast";
  const std::string adj = "%" + p + ".cadj";
  const std::string result = "%" + p + ".result";

  if (widthBitsOf(to) < widthBitsOf(from)) {
    out << "  " << mid << " = trunc " << ty << " " << input << " to " << sty
        << "\n";
    out << "  " << adj << " = add " << sty << " " << mid << ", 1\n";
    out << "  " << result << " = " << widen << " " << sty << " " << adj << " to "
        << ty << "\n";
  } else {
    out << "  " << mid << " = " << widen << " " << ty << " " << input << " to "
        << sty << "\n";
    out << "  " << adj << " = add " << sty << " " << mid << ", 1\n";
    out << "  " << result << " = trunc " << sty << " " << adj << " to " << ty
        << "\n";
  }
  return {result, entryBlock};
}

std::string composeExtra(std::ostream &out, const GeneratorConfig &config,
                         const std::string &p, const std::string &input,
                         const std::string &ty) {
  const std::string v = "%" + p + ".extra";
  const std::string flags =
      std::string(hasNuw(config) ? " nuw" : "") + (hasNsw(config) ? " nsw" : "");
  switch (extraOpcode(config)) {
  case ExtraOpcode::None:
    return input;
  case ExtraOpcode::AddZero:
    out << "  " << v << " = add" << flags << " " << ty << " " << input
        << ", 0\n";
    return v;
  case ExtraOpcode::MulOne:
    out << "  " << v << " = mul" << flags << " " << ty << " " << input
        << ", 1\n";
    return v;
  case ExtraOpcode::XorSelf:
    out << "  " << v << " = xor " << ty << " " << input << ", " << input << "\n";
    return v;
  case ExtraOpcode::DeadAdd:
    out << "  %" << p << ".dead = add " << ty << " %a, " << config.constB << "\n";
    return input;
  case ExtraOpcode::AndSelf:
    out << "  " << v << " = and " << ty << " " << input << ", " << input << "\n";
    return v;
  }
  return input;
}

GeneratedIR generateComposed(const GeneratorConfig &config) {
  const std::uint8_t bits = config.composeBits;
  const bool composeVec =
      (bits & ComposeVector) != 0U && vectorShape(config) != VectorShape::None;
  const bool composeGlobal =
      (bits & ComposeGlobal) != 0U && globalShape(config) != GlobalShape::None;

  std::ostringstream out;
  out << "; ModuleID = 'o2t-generated'\n";
  out << "source_filename = \"o2t-generated.ll\"\n";
  emitMetadata(out, config);
  out << "\n";

  // Module-scope prologue: reduction declares the vector stage needs, and a
  // composed global (kept at module scope so its dead-initializer semantics are
  // unchanged while @test carries the in-function regions).
  if (composeVec) {
    const std::string declare = composedVectorDeclare(config);
    if (!declare.empty()) {
      out << declare << "\n";
    }
  }
  if (composeGlobal) {
    emitGlobalDeadInitializer(out, config);
    out << "\n";
  }

  const std::string ty = intTy(config);
  out << "define " << ty << " @test(" << composedSignature(config, ty)
      << ") {\n";
  out << "entry:\n";

  std::string value = "%a";
  std::string block = "entry";
  // Fold any extra scalar args (%c, %d) into the threaded value so they are live
  // and give reassociation/CSE/GVN real operand material.
  const unsigned scalars = scalarArgCount(config);
  for (unsigned i = 2; i < scalars; ++i) {
    const std::string folded = "%argfold" + std::to_string(i);
    out << "  " << folded << " = add " << ty << " " << value << ", "
        << scalarArgName(i) << "\n";
    value = folded;
  }
  int stage = 0;

  if ((bits & ComposeCfg) != 0U) {
    const std::string p = "s" + std::to_string(stage++);
    const StageOut r = composeCfgStage(out, config, p, value, block, ty);
    value = r.value;
    block = r.block;
  }
  if ((bits & ComposeMemory) != 0U && memoryShape(config) != MemoryShape::None) {
    const std::string p = "s" + std::to_string(stage++);
    const StageOut r = composeMemoryStage(out, config, p, value, block, ty);
    value = r.value;
    block = r.block;
  }
  if ((bits & ComposeLoop) != 0U && loopShape(config) != LoopShape::None) {
    const std::string p = "s" + std::to_string(stage++);
    const StageOut r = composeLoopStage(out, config, p, value, block, ty);
    value = r.value;
    block = r.block;
  }
  if (composeVec) {
    const std::string p = "s" + std::to_string(stage++);
    const StageOut r = composeVectorStage(out, config, p, value, block, ty);
    value = r.value;
    block = r.block;
  }
  if ((bits & ComposeCast) != 0U) {
    const std::string p = "s" + std::to_string(stage++);
    const StageOut r = composeCastStage(out, config, p, value, block, ty);
    value = r.value;
    block = r.block;
  }

  value = composeExtra(out, config, "fin", value, ty);
  out << "  ret " << ty << " " << value << "\n";
  out << "}\n";

  return GeneratedIR{out.str(), coverageFor(config)};
}

} // namespace

GeneratedIR generateIR(const GeneratorConfig &rawConfig) {
  const GeneratorConfig config = normalizeConfig(rawConfig);

  // Composed mode threads CFG/memory/loop/vector regions through one function
  // and emits a composed global at module scope. composeBits selects which
  // dimensions participate; composeBits == 0 keeps the legacy cascade.
  if (config.composeBits != 0) {
    return generateComposed(config);
  }

  std::ostringstream out;

  out << "; ModuleID = 'o2t-generated'\n";
  out << "source_filename = \"o2t-generated.ll\"\n";
  emitMetadata(out, config);
  out << "\n";
  if (globalShape(config) != GlobalShape::None) {
    emitGlobalDeadInitializer(out, config);
    return GeneratedIR{out.str(), coverageFor(config)};
  }
  if (vectorShape(config) == VectorShape::ReductionAddZero ||
      vectorShape(config) == VectorShape::ReductionAddSingleLane) {
    out << "declare i32 @llvm.vector.reduce.add.v4i32(<4 x i32>)\n\n";
  } else if (vectorShape(config) == VectorShape::ScalableReductionAddZero) {
    out << "declare i32 @llvm.vector.reduce.add.nxv4i32(<vscale x 4 x i32>)\n\n";
  }
  out << "define i32 @test(i32 %a, i32 %b) {\n";
  out << "entry:\n";

  if (vectorShape(config) != VectorShape::None) {
    emitVector(out, config);
  } else {
  switch (memoryShape(config)) {
  case MemoryShape::None:
    if (loopShape(config) != LoopShape::None) {
      emitLoop(out, config);
      break;
    }
    switch (shape(config)) {
    case Shape::StraightLine:
      emitStraightLine(out, config);
      break;
    case Shape::Diamond:
      emitDiamond(out, config);
      break;
    case Shape::NestedDiamond:
      emitNestedDiamond(out, config);
      break;
    case Shape::UnreachableTail:
      emitUnreachableTail(out, config);
      break;
    case Shape::SwitchLikeChain:
      emitSwitchLikeChain(out, config);
      break;
    }
    break;
  case MemoryShape::AllocaStoreLoad:
    emitAllocaStoreLoad(out, config);
    break;
  case MemoryShape::LoadAfterStore:
    emitLoadAfterStore(out, config);
    break;
  case MemoryShape::DeadStore:
    emitDeadStore(out, config);
    break;
  case MemoryShape::OverwrittenStore:
    emitOverwrittenStore(out, config);
    break;
  case MemoryShape::UnusedAlloca:
    emitUnusedAlloca(out, config);
    break;
  }
  }

  out << "}\n";

  return GeneratedIR{out.str(), coverageFor(config)};
}

} // namespace cv
