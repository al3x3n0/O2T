#pragma once

#include "llvm/ADT/StringRef.h"

#include <array>
#include <cctype>
#include <string>

namespace cv {

struct SourceMarkerPattern {
  const char *marker;
  const char *pattern;
  const char *requiredTokens;
  const char *forbiddenTokens;
};

inline constexpr std::array<SourceMarkerPattern, 108>
    kSourceMarkerPatterns{{
        {"probe.cleanup.unused-alloca", "hasNUsesOrMore(1)", "!	if", ""},
        {"probe.cleanup.unused-alloca", "users().empty", "if", ""},
        {"probe.cleanup.unused-alloca", "hasNUses(0)", "if", ""},
        {"probe.cleanup.unused-alloca", "user_empty", "if", ""},
        {"probe.cleanup.unused-alloca", "use_empty", "if", ""},
        {"probe.instcombine.and-allones", "m_c_And(", "m_AllOnes(", ""},
        {"probe.instcombine.and-self", "m_c_And(", "", "m_AllOnes("},
        {"probe.instcombine.and-allones", "m_And(", "m_AllOnes(", ""},
        {"probe.instcombine.and-self", "m_And(", "", "m_AllOnes("},
        {"probe.vector.reduction-add-single-lane", "vector reduce add single lane", "", ""},
        {"probe.dce.dead-instruction", "isInstructionTriviallyDead(", "", ""},
        {"probe.vector.scalable.and-allones", "scalable vector and allones", "", ""},
        {"probe.vector.scalable.reduction-add-zero", "scalable reduction add zero", "", ""},
        {"probe.loop.simple-trip-count", "getSmallConstantTripCount", "", ""},
        {"probe.globalopt.dead-initializer", "isGlobalInitializerDead(", "", ""},
        {"probe.instcombine.redundant-load", "FindAvailableLoadedValue", "", ""},
        {"probe.mem2reg.store-load-forward", "rewriteSingleStoreAlloca", "", ""},
        {"probe.vector.extract-insert", "extract insert same lane", "", ""},
        {"probe.vector.scalable.add-zero", "scalable vector add zero", "", ""},
        {"probe.vector.scalable.and-allones", "ScalableVectorAndAllOnes", "", ""},
        {"probe.vector.scalable.reduction-add-zero", "ScalableReductionAddZero", "", ""},
        {"probe.vector.scalable.sub-zero", "scalable vector sub zero", "", ""},
        {"probe.vector.scalable.xor-self", "scalable vector xor self", "", ""},
        {"probe.vector.insert-extract-identity", "insert extract identity", "", ""},
        {"probe.vector.scalable.mul-one", "scalable vector mul one", "", ""},
        {"probe.vector.scalable.or-zero", "scalable vector or zero", "", ""},
        {"probe.vector.add-zero", "m_SplatOrPoison(m_Zero", "", ""},
        {"probe.vector.reduction-add-single-lane", "ReductionAddSingleLane", "", ""},
        {"probe.dce.dead-loop-instruction", "isDeadLoopInstruction", "", ""},
        {"probe.simplifycfg.diamond", "getSinglePredecessor(", "", ""},
        {"probe.vector.extract-insert", "sameLaneExtractInsert", "", ""},
        {"probe.vector.insert-extract-identity", "insertExtractIdentity", "", ""},
        {"probe.vector.mul-one", "m_SplatOrPoison(m_One", "", ""},
        {"probe.vector.scalable.add-zero", "ScalableVectorAddZero", "", ""},
        {"probe.vector.scalable.sub-zero", "ScalableVectorSubZero", "", ""},
        {"probe.vector.scalable.xor-self", "ScalableVectorXorSelf", "", ""},
        {"probe.vector.shuffle-identity", "isIdentityWithExtract", "", ""},
        {"probe.vector.scalable.mul-one", "ScalableVectorMulOne", "", ""},
        {"probe.vector.scalable.or-zero", "ScalableVectorOrZero", "", ""},
        {"probe.loop.induction-phi", "InductionDescriptor", "", ""},
        {"probe.mem2reg.promotable-alloca", "isAllocaPromotable(", "", ""},
        {"probe.simplifycfg.diamond", "getSingleSuccessor(", "", ""},
        {"probe.mem2reg.store-load-forward", "OnlyUsedInOneBlock", "", ""},
        {"probe.vector.and-allones", "vector and allones", "", ""},
        {"probe.licm.invariant-op", "makeLoopInvariant", "", ""},
        {"probe.vector.reduction-add-zero", "vector_reduce_add", "", ""},
        {"probe.vector.and-allones", "VectorAndAllOnes", "", ""},
        {"probe.vector.reduction-add-zero", "ReductionAddZero", "", ""},
        {"probe.licm.invariant-op", "isLoopInvariant", "", ""},
        {"probe.loop.simple-trip-count", "ScalarEvolution", "", ""},
        {"probe.mem2reg.promotable-alloca", "PromoteMemToReg", "", ""},
        {"probe.simplifycfg.unreachable-block", "UnreachableInst", "", ""},
        {"probe.vector.add-zero", "vector add zero", "", ""},
        {"probe.vector.reduction-add-zero", "CreateAddReduce", "", ""},
        {"probe.vector.sub-zero", "vector sub zero", "", ""},
        {"probe.dse.overwritten-store", "getLocForWrite", "", ""},
        {"probe.vector.mul-one", "vector mul one", "", ""},
        {"probe.vector.or-zero", "vector or zero", "", ""},
        {"probe.vector.shuffle-identity", "isIdentityMask", "", ""},
        {"probe.simplifycfg.loop-exit", "exiting block", "", ""},
        {"probe.simplifycfg.nested-branch", "nested branch", "", ""},
        {"probe.vector.add-zero", "VectorAddZero", "", ""},
        {"probe.vector.sub-zero", "VectorSubZero", "", ""},
        {"probe.vector.xor-self", "VectorXorSelf", "", ""},
        {"probe.loop.canonical-header", "LoopSimplify", "", ""},
        {"probe.simplifycfg.loop-exit", "getExitBlock", "", ""},
        {"probe.simplifycfg.nested-branch", "NestedBranch", "", ""},
        {"probe.vector.mul-one", "VectorMulOne", "", ""},
        {"probe.vector.or-zero", "VectorOrZero", "", ""},
        {"probe.vector.xor-self", "Vec0 == Vec1", "", ""},
        {"probe.dse.dead-store", "isRemovable", "", ""},
        {"probe.dse.overwritten-store", "isOverwrite", "", ""},
        {"probe.simplifycfg.unreachable-block", "unreachable", "", ""},
        {"probe.vector.shuffle-splat", "isSplatMask", "", ""},
        {"probe.instcombine.and-allones", "m_AllOnes(", "", ""},
        {"probe.instcombine.xor-self", "LHS == RHS", "", ""},
        {"probe.instcombine.xor-self", "Op0 == Op1", "", ""},
        {"probe.loop.canonical-header", "getHeader(", "", ""},
        {"probe.simplifycfg.branch-chain", "SwitchInst", "", ""},
        {"probe.vector.smax", "CreateSMax", "", ""},
        {"probe.vector.smax", "VectorSMax", "", ""},
        {"probe.vector.smin", "CreateSMin", "", ""},
        {"probe.vector.smin", "VectorSMin", "", ""},
        {"probe.vector.umax", "CreateUMax", "", ""},
        {"probe.vector.umax", "VectorUMax", "", ""},
        {"probe.vector.umin", "CreateUMin", "", ""},
        {"probe.vector.umin", "VectorUMin", "", ""},
        {"probe.dse.dead-store", "DeadStore", "", ""},
        {"probe.vector.abs", "CreateAbs", "", ""},
        {"probe.vector.abs", "VectorAbs", "", ""},
        {"probe.dce.dead-loop-instruction", "LoopInfo", "", ""},
        {"probe.instcombine.add-zero", "m_c_Add(", "", ""},
        {"probe.instcombine.mul-one", "m_c_Mul(", "", ""},
        {"probe.instcombine.redundant-load", "LoadInst", "", ""},
        {"probe.instcombine.or-zero", "m_c_Or(", "", ""},
        {"probe.loop.induction-phi", "PHINode", "", ""},
        {"probe.instcombine.add-zero", "m_Add(", "", ""},
        {"probe.instcombine.mul-one", "m_Mul(", "", ""},
        {"probe.instcombine.sub-zero", "m_Sub(", "", ""},
        {"probe.simplifycfg.branch-chain", "switch", "", ""},
        {"probe.instcombine.or-zero", "m_Or(", "", ""},
        {"probe.vector.smax", "smax", "", ""},
        {"probe.vector.smin", "smin", "", ""},
        {"probe.vector.umax", "umax", "", ""},
        {"probe.vector.umin", "umin", "", ""},
        {"probe.vector.abs", "abs", "", ""},
        {"probe.instcombine.add-zero", "m_Zero(", "", ""},
        {"probe.instcombine.mul-one", "m_One(", "", ""},
    }};

inline bool generatedSourceTokensAllPresent(llvm::StringRef Text,
                                                llvm::StringRef Tokens) {
  while (!Tokens.empty()) {
    auto Split = Tokens.split('\t');
    if (!Split.first.empty() && !Text.contains(Split.first)) {
      return false;
    }
    Tokens = Split.second;
  }
  return true;
}

inline bool generatedSourceTokensAnyPresent(llvm::StringRef Text,
                                               llvm::StringRef Tokens) {
  while (!Tokens.empty()) {
    auto Split = Tokens.split('\t');
    if (!Split.first.empty() && Text.contains(Split.first)) {
      return true;
    }
    Tokens = Split.second;
  }
  return false;
}

inline std::string generatedSourceCompact(llvm::StringRef Text) {
  std::string Result;
  Result.reserve(Text.size());
  for (char C : Text) {
    if (!std::isspace(static_cast<unsigned char>(C))) {
      Result.push_back(C);
    }
  }
  return Result;
}

inline std::string generatedSourceCompactTokens(llvm::StringRef Tokens) {
  std::string Result;
  bool First = true;
  while (!Tokens.empty()) {
    auto Split = Tokens.split('\t');
    if (!First) {
      Result.push_back('\t');
    }
    First = false;
    Result += generatedSourceCompact(Split.first);
    Tokens = Split.second;
  }
  return Result;
}

inline std::string markerForGeneratedSourceText(llvm::StringRef Text) {
  const std::string CompactTextStorage = generatedSourceCompact(Text);
  const llvm::StringRef CompactText(CompactTextStorage);
  for (const SourceMarkerPattern &Entry : kSourceMarkerPatterns) {
    const std::string CompactPatternStorage =
        generatedSourceCompact(Entry.pattern);
    const std::string CompactRequiredStorage =
        generatedSourceCompactTokens(Entry.requiredTokens);
    const std::string CompactForbiddenStorage =
        generatedSourceCompactTokens(Entry.forbiddenTokens);
    if ((Text.contains(Entry.pattern) ||
         (!CompactPatternStorage.empty() &&
          CompactText.contains(CompactPatternStorage))) &&
        (generatedSourceTokensAllPresent(Text, Entry.requiredTokens) ||
         generatedSourceTokensAllPresent(CompactText, CompactRequiredStorage)) &&
        !generatedSourceTokensAnyPresent(Text, Entry.forbiddenTokens) &&
        !generatedSourceTokensAnyPresent(CompactText, CompactForbiddenStorage)) {
      return Entry.marker;
    }
  }
  return "";
}

} // namespace cv
