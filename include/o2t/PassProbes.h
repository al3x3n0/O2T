#pragma once

#include "o2t/AbstractIR.h"

namespace cv {

struct PassProbeCoverage {
  bool instcombineAddZero = false;
  bool instcombineSubZero = false;
  bool instcombineMulOne = false;
  bool instcombineXorSelf = false;
  bool instcombineOrZero = false;
  bool instcombineAndAllOnes = false;
  bool instcombineAndSelf = false;
  bool dceDeadInstruction = false;
  bool simplifycfgUnreachableBlock = false;
  bool simplifycfgDiamond = false;
  bool simplifycfgNestedBranch = false;
  bool simplifycfgBranchChain = false;
  bool mem2regPromotableAlloca = false;
  bool mem2regStoreLoadForward = false;
  bool dseDeadStore = false;
  bool dseOverwrittenStore = false;
  bool instcombineRedundantLoad = false;
  bool cleanupUnusedAlloca = false;
  bool loopCanonicalHeader = false;
  bool loopInductionPhi = false;
  bool loopSimpleTripCount = false;
  bool licmInvariantOp = false;
  bool dceDeadLoopInstruction = false;
  bool simplifycfgLoopExit = false;
  bool vectorAddZero = false;
  bool vectorMulOne = false;
  bool vectorXorSelf = false;
  bool vectorShuffleIdentity = false;
  bool vectorShuffleSplat = false;
  bool vectorExtractInsert = false;
  bool vectorReductionAddZero = false;
  bool vectorSubZero = false;
  bool vectorOrZero = false;
  bool vectorAndAllOnes = false;
  bool vectorInsertExtractIdentity = false;
  bool vectorReductionAddSingleLane = false;
  bool vectorScalableAddZero = false;
  bool vectorScalableMulOne = false;
  bool vectorScalableXorSelf = false;
  bool vectorScalableSubZero = false;
  bool vectorScalableOrZero = false;
  bool vectorScalableAndAllOnes = false;
  bool vectorScalableReductionAddZero = false;
  bool vectorSMin = false;
  bool vectorSMax = false;
  bool vectorUMin = false;
  bool vectorUMax = false;
  bool vectorAbs = false;
  bool globalDeadInitializer = false;
};

inline bool isConstant(const AbstractInstruction &instruction,
                       std::int32_t value) {
  return instruction.rhs == OperandKind::Constant &&
         instruction.rhsConstant == value;
}

inline bool isAddZeroFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::Add &&
         isConstant(instruction, 0);
}

inline bool isMulOneFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::Mul &&
         isConstant(instruction, 1);
}

inline bool isSubZeroFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::Sub &&
         isConstant(instruction, 0);
}

inline bool isXorSelfFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::Xor &&
         instruction.rhs == OperandKind::SameAsLhs;
}

inline bool isOrZeroFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::Or &&
         isConstant(instruction, 0);
}

inline bool isAndAllOnesFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::And &&
         isConstant(instruction, -1);
}

inline bool isAndSelfFold(const AbstractInstruction &instruction) {
  return instruction.opcode == AbstractOpcode::And &&
         instruction.rhs == OperandKind::SameAsLhs;
}

inline bool hasUnreachableBlock(const AbstractFunction &function) {
  for (std::uint8_t index = 0; index < function.blockCount; ++index) {
    if (!function.blocks[index].reachable) {
      return true;
    }
  }
  return false;
}

inline std::uint8_t branchBlockCount(const AbstractFunction &function) {
  std::uint8_t count = 0;
  for (std::uint8_t index = 0; index < function.blockCount; ++index) {
    if (function.blocks[index].branches) {
      ++count;
    }
  }
  return count;
}

inline bool hasMergeBlock(const AbstractFunction &function) {
  for (std::uint8_t index = 0; index < function.blockCount; ++index) {
    if (function.blocks[index].merges) {
      return true;
    }
  }
  return false;
}

inline bool hasOpcode(const AbstractFunction &function, AbstractOpcode opcode) {
  for (std::uint8_t index = 0; index < function.instructionCount; ++index) {
    if (function.instructions[index].opcode == opcode) {
      return true;
    }
  }
  return false;
}

inline std::uint8_t opcodeCount(const AbstractFunction &function,
                                AbstractOpcode opcode) {
  std::uint8_t count = 0;
  for (std::uint8_t index = 0; index < function.instructionCount; ++index) {
    if (function.instructions[index].opcode == opcode) {
      ++count;
    }
  }
  return count;
}

inline PassProbeCoverage scanOptimizationProbes(
    const AbstractFunction &function) {
  PassProbeCoverage coverage;

  if (function.globalShape != GlobalShape::None) {
    coverage.globalDeadInitializer = true;
    return coverage;
  }

  if (function.vectorShape != VectorShape::None) {
    coverage.vectorAddZero = function.vectorShape == VectorShape::AddZero;
    coverage.vectorMulOne = function.vectorShape == VectorShape::MulOne;
    coverage.vectorXorSelf = function.vectorShape == VectorShape::XorSelf;
    coverage.vectorShuffleIdentity =
        function.vectorShape == VectorShape::ShuffleIdentity;
    coverage.vectorShuffleSplat = function.vectorShape == VectorShape::ShuffleSplat;
    coverage.vectorExtractInsert = function.vectorShape == VectorShape::ExtractInsert;
    coverage.vectorReductionAddZero =
        function.vectorShape == VectorShape::ReductionAddZero;
    coverage.vectorSubZero = function.vectorShape == VectorShape::SubZero;
    coverage.vectorOrZero = function.vectorShape == VectorShape::OrZero;
    coverage.vectorAndAllOnes =
        function.vectorShape == VectorShape::AndAllOnes;
    coverage.vectorInsertExtractIdentity =
        function.vectorShape == VectorShape::InsertExtractIdentity;
    coverage.vectorReductionAddSingleLane =
        function.vectorShape == VectorShape::ReductionAddSingleLane;
    coverage.vectorScalableAddZero =
        function.vectorShape == VectorShape::ScalableAddZero;
    coverage.vectorScalableMulOne =
        function.vectorShape == VectorShape::ScalableMulOne;
    coverage.vectorScalableXorSelf =
        function.vectorShape == VectorShape::ScalableXorSelf;
    coverage.vectorScalableSubZero =
        function.vectorShape == VectorShape::ScalableSubZero;
    coverage.vectorScalableOrZero =
        function.vectorShape == VectorShape::ScalableOrZero;
    coverage.vectorScalableAndAllOnes =
        function.vectorShape == VectorShape::ScalableAndAllOnes;
    coverage.vectorScalableReductionAddZero =
        function.vectorShape == VectorShape::ScalableReductionAddZero;
    coverage.vectorSMin = function.vectorShape == VectorShape::SMin;
    coverage.vectorSMax = function.vectorShape == VectorShape::SMax;
    coverage.vectorUMin = function.vectorShape == VectorShape::UMin;
    coverage.vectorUMax = function.vectorShape == VectorShape::UMax;
    coverage.vectorAbs = function.vectorShape == VectorShape::Abs;
    return coverage;
  }

  for (std::uint8_t index = 0; index < function.instructionCount; ++index) {
    const AbstractInstruction &instruction = function.instructions[index];
    coverage.instcombineAddZero =
        coverage.instcombineAddZero || isAddZeroFold(instruction);
    coverage.instcombineSubZero =
        coverage.instcombineSubZero || isSubZeroFold(instruction);
    coverage.instcombineMulOne =
        coverage.instcombineMulOne || isMulOneFold(instruction);
    coverage.instcombineXorSelf =
        coverage.instcombineXorSelf || isXorSelfFold(instruction);
    coverage.instcombineOrZero =
        coverage.instcombineOrZero || isOrZeroFold(instruction);
    coverage.instcombineAndAllOnes =
        coverage.instcombineAndAllOnes || isAndAllOnesFold(instruction);
    coverage.instcombineAndSelf =
        coverage.instcombineAndSelf || isAndSelfFold(instruction);
    coverage.dceDeadInstruction =
        coverage.dceDeadInstruction ||
        (instruction.isDead && instruction.opcode != AbstractOpcode::Store &&
         function.loopShape != LoopShape::DeadBodyLoop);
    coverage.dseDeadStore =
        coverage.dseDeadStore ||
        (instruction.isDead && instruction.opcode == AbstractOpcode::Store &&
         function.memoryShape == MemoryShape::DeadStore);
    coverage.dseOverwrittenStore =
        coverage.dseOverwrittenStore ||
        (instruction.isDead && instruction.opcode == AbstractOpcode::Store &&
         function.memoryShape == MemoryShape::OverwrittenStore);
  }

  const std::uint8_t branches = branchBlockCount(function);
  const bool hasAlloca = hasOpcode(function, AbstractOpcode::Alloca);
  const bool hasStore = hasOpcode(function, AbstractOpcode::Store);
  const bool hasLoad = hasOpcode(function, AbstractOpcode::Load);
  coverage.simplifycfgUnreachableBlock = hasUnreachableBlock(function);
  coverage.simplifycfgDiamond = branches == 1 && hasMergeBlock(function);
  coverage.simplifycfgNestedBranch = branches > 1 &&
                                     function.shape == Shape::NestedDiamond;
  coverage.simplifycfgBranchChain = branches > 1 &&
                                    function.shape == Shape::SwitchLikeChain;
  coverage.mem2regPromotableAlloca =
      hasAlloca && function.memoryShape != MemoryShape::UnusedAlloca &&
      function.memoryShape != MemoryShape::None;
  coverage.mem2regStoreLoadForward = hasStore && hasLoad;
  coverage.instcombineRedundantLoad = opcodeCount(function, AbstractOpcode::Load) > 1;
  coverage.cleanupUnusedAlloca =
      hasAlloca && !hasStore && !hasLoad &&
      function.memoryShape == MemoryShape::UnusedAlloca;
  coverage.loopCanonicalHeader = function.loopShape != LoopShape::None;
  coverage.loopInductionPhi =
      function.loopShape != LoopShape::None &&
      hasOpcode(function, AbstractOpcode::Phi);
  coverage.loopSimpleTripCount =
      function.loopShape != LoopShape::None &&
      hasOpcode(function, AbstractOpcode::Icmp);
  coverage.licmInvariantOp =
      function.loopShape == LoopShape::InvariantOpLoop;
  coverage.dceDeadLoopInstruction =
      function.loopShape == LoopShape::DeadBodyLoop;
  coverage.simplifycfgLoopExit =
      function.loopShape != LoopShape::None && branches > 0;
  return coverage;
}

} // namespace cv
