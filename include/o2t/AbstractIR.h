#pragma once

#include "o2t/GeneratorConfig.h"

#include <cstdint>

namespace cv {

enum class AbstractOpcode : std::uint8_t {
  Add = 0,
  Sub = 1,
  Mul = 2,
  Xor = 3,
  Or = 4,
  And = 5,
  Icmp = 6,
  Phi = 7,
  Branch = 8,
  Return = 9,
  Unreachable = 10,
  Alloca = 11,
  Load = 12,
  Store = 13,
  Gep = 14,
};

enum class OperandKind : std::uint8_t {
  ArgumentA = 0,
  ArgumentB = 1,
  Constant = 2,
  SameAsLhs = 3,
  Value = 4,
};

struct AbstractInstruction {
  AbstractOpcode opcode = AbstractOpcode::Add;
  OperandKind lhs = OperandKind::ArgumentA;
  OperandKind rhs = OperandKind::Constant;
  std::int32_t rhsConstant = 0;
  std::uint8_t block = 0;
  bool isDead = false;
  std::uint8_t memorySlot = 0;
};

struct AbstractBlock {
  bool reachable = true;
  bool branches = false;
  bool merges = false;
};

struct AbstractFunction {
  static constexpr std::uint8_t MaxInstructions = 16;
  static constexpr std::uint8_t MaxBlocks = 8;

  AbstractInstruction instructions[MaxInstructions]{};
  AbstractBlock blocks[MaxBlocks]{};
  std::uint8_t instructionCount = 0;
  std::uint8_t blockCount = 0;
  Shape shape = Shape::StraightLine;
  MemoryShape memoryShape = MemoryShape::None;
  LoopShape loopShape = LoopShape::None;
  VectorShape vectorShape = VectorShape::None;
  GlobalShape globalShape = GlobalShape::None;
};

inline AbstractOpcode abstractOpcodeFromArith(std::uint8_t opcode) {
  switch (opcode % 6) {
  case 0:
    return AbstractOpcode::Add;
  case 1:
    return AbstractOpcode::Sub;
  case 2:
    return AbstractOpcode::Mul;
  case 3:
    return AbstractOpcode::Xor;
  case 4:
    return AbstractOpcode::Or;
  case 5:
    return AbstractOpcode::And;
  }
  return AbstractOpcode::Add;
}

inline OperandKind rhsOperandKind(std::uint8_t rhsMode) {
  switch (rhsMode % 4) {
  case 0:
  case 1:
  case 3:
    return OperandKind::Constant;
  case 2:
    return OperandKind::ArgumentB;
  }
  return OperandKind::Constant;
}

inline std::int32_t rhsConstantValue(const GeneratorConfig &config) {
  switch (config.rhsMode % 4) {
  case 0:
    return 0;
  case 1:
    return 1;
  case 3:
    return config.constA;
  case 2:
    return 0;
  }
  return 0;
}

inline void addInstruction(AbstractFunction &function,
                           AbstractInstruction instruction) {
  if (function.instructionCount < AbstractFunction::MaxInstructions) {
    function.instructions[function.instructionCount] = instruction;
    ++function.instructionCount;
  }
}

inline void addArith(AbstractFunction &function, const GeneratorConfig &config,
                     std::uint8_t block, bool dead = false,
                     OperandKind lhs = OperandKind::ArgumentA) {
  AbstractInstruction instruction;
  instruction.opcode = abstractOpcodeFromArith(config.arithOpcode);
  instruction.lhs = lhs;
  instruction.rhs = rhsOperandKind(config.rhsMode);
  instruction.rhsConstant = rhsConstantValue(config);
  instruction.block = block;
  instruction.isDead = dead;
  addInstruction(function, instruction);
}

inline void addExtra(AbstractFunction &function, const GeneratorConfig &config,
                     std::uint8_t block) {
  AbstractInstruction instruction;
  instruction.block = block;
  instruction.lhs = OperandKind::Value;

  switch (config.extraOpcode % 6) {
  case 0:
    return;
  case 1:
    instruction.opcode = AbstractOpcode::Add;
    instruction.rhs = OperandKind::Constant;
    instruction.rhsConstant = 0;
    break;
  case 2:
    instruction.opcode = AbstractOpcode::Mul;
    instruction.rhs = OperandKind::Constant;
    instruction.rhsConstant = 1;
    break;
  case 3:
    instruction.opcode = AbstractOpcode::Xor;
    instruction.rhs = OperandKind::SameAsLhs;
    instruction.rhsConstant = 0;
    break;
  case 4:
    instruction.opcode = AbstractOpcode::Add;
    instruction.rhs = OperandKind::Constant;
    instruction.rhsConstant = config.constB;
    instruction.isDead = true;
    break;
  case 5:
    instruction.opcode = AbstractOpcode::And;
    instruction.rhs = OperandKind::SameAsLhs;
    instruction.rhsConstant = 0;
    break;
  }

  addInstruction(function, instruction);
}

inline void initBlocks(AbstractFunction &function, std::uint8_t count);

inline void addMemoryInstruction(AbstractFunction &function,
                                 AbstractOpcode opcode, std::uint8_t block,
                                 std::uint8_t slot, bool dead = false) {
  AbstractInstruction instruction;
  instruction.opcode = opcode;
  instruction.block = block;
  instruction.memorySlot = slot;
  instruction.isDead = dead;
  addInstruction(function, instruction);
}

inline std::uint8_t activeMemorySlot(const GeneratorConfig &config) {
  return config.pointerMode % 3 == static_cast<std::uint8_t>(PointerMode::SecondSlot)
             ? 1
             : 0;
}

inline void addStackSlot(AbstractFunction &function, const GeneratorConfig &config,
                         std::uint8_t block) {
  addMemoryInstruction(function, AbstractOpcode::Alloca, block, 0);
  if (config.pointerMode % 3 == static_cast<std::uint8_t>(PointerMode::SecondSlot)) {
    addMemoryInstruction(function, AbstractOpcode::Alloca, block, 1);
  }
  if (config.pointerMode % 3 == static_cast<std::uint8_t>(PointerMode::IndexedSlot)) {
    addMemoryInstruction(function, AbstractOpcode::Gep, block, 0);
  }
}

inline void buildMemoryAbstractFunction(AbstractFunction &function,
                                        const GeneratorConfig &config) {
  initBlocks(function, config.storeMode % 3 == static_cast<std::uint8_t>(StoreMode::ConditionalStore) ? 4 : 1);
  function.memoryShape = static_cast<MemoryShape>(config.memoryShape % 6);
  const std::uint8_t slot = activeMemorySlot(config);
  addStackSlot(function, config, 0);

  switch (function.memoryShape) {
  case MemoryShape::None:
    return;
  case MemoryShape::AllocaStoreLoad:
    if (config.storeMode % 3 == static_cast<std::uint8_t>(StoreMode::ConditionalStore)) {
      function.blocks[0].branches = true;
      function.blocks[3].merges = true;
      addMemoryInstruction(function, AbstractOpcode::Store, 1, slot);
      addMemoryInstruction(function, AbstractOpcode::Store, 2, slot);
      addMemoryInstruction(function, AbstractOpcode::Load, 3, slot);
    } else {
      addMemoryInstruction(function, AbstractOpcode::Store, 0, slot);
      if (config.storeMode % 3 == static_cast<std::uint8_t>(StoreMode::DoubleStore)) {
        addMemoryInstruction(function, AbstractOpcode::Store, 0, slot, true);
      }
      addMemoryInstruction(function, AbstractOpcode::Load, 0, slot);
    }
    return;
  case MemoryShape::LoadAfterStore:
    addMemoryInstruction(function, AbstractOpcode::Store, 0, slot);
    addMemoryInstruction(function, AbstractOpcode::Load, 0, slot);
    addMemoryInstruction(function, AbstractOpcode::Load, 0, slot);
    return;
  case MemoryShape::DeadStore:
    addMemoryInstruction(function, AbstractOpcode::Store, 0, slot, true);
    return;
  case MemoryShape::OverwrittenStore:
    addMemoryInstruction(function, AbstractOpcode::Store, 0, slot, true);
    addMemoryInstruction(function, AbstractOpcode::Store, 0, slot);
    addMemoryInstruction(function, AbstractOpcode::Load, 0, slot);
    return;
  case MemoryShape::UnusedAlloca:
    return;
  }
}

inline void buildLoopAbstractFunction(AbstractFunction &function,
                                      const GeneratorConfig &config) {
  initBlocks(function, 5);
  function.loopShape = static_cast<LoopShape>(config.loopShape % 5);
  function.blocks[1].branches = true;
  function.blocks[3].branches = true;
  function.blocks[4].merges = true;

  addMemoryInstruction(function, AbstractOpcode::Branch, 0, 0);
  addMemoryInstruction(function, AbstractOpcode::Phi, 1, 0);
  addMemoryInstruction(function, AbstractOpcode::Icmp, 1, 0);
  addMemoryInstruction(function, AbstractOpcode::Branch, 1, 0);

  if (function.loopShape == LoopShape::InvariantOpLoop) {
    addArith(function, config, 2, false, OperandKind::ArgumentB);
  } else if (function.loopShape == LoopShape::DeadBodyLoop) {
    addArith(function, config, 2, true, OperandKind::Value);
  } else {
    addArith(function, config, 2, false, OperandKind::Value);
  }

  if (function.loopShape == LoopShape::EarlyExitLoop) {
    addMemoryInstruction(function, AbstractOpcode::Icmp, 2, 0);
    addMemoryInstruction(function, AbstractOpcode::Branch, 2, 0);
  }

  addArith(function, config, 3, false, OperandKind::Value);
  addMemoryInstruction(function, AbstractOpcode::Branch, 3, 0);
}

inline void initBlocks(AbstractFunction &function, std::uint8_t count) {
  function.blockCount = count;
  for (std::uint8_t index = 0; index < AbstractFunction::MaxBlocks; ++index) {
    function.blocks[index] = AbstractBlock{};
    function.blocks[index].reachable = index < count;
  }
}

inline AbstractFunction buildAbstractFunction(const GeneratorConfig &config) {
  AbstractFunction function;
  function.shape = static_cast<Shape>(config.shape % 5);
  function.memoryShape = static_cast<MemoryShape>(config.memoryShape % 6);
  function.loopShape = static_cast<LoopShape>(config.loopShape % 5);
  function.vectorShape = static_cast<VectorShape>(config.vectorShape % 25);
  function.globalShape = static_cast<GlobalShape>(config.globalShape % 4);

  if (function.globalShape != GlobalShape::None) {
    initBlocks(function, 1);
    return function;
  }

  if (function.vectorShape != VectorShape::None) {
    initBlocks(function, 1);
    return function;
  }

  if (function.memoryShape != MemoryShape::None) {
    buildMemoryAbstractFunction(function, config);
    return function;
  }
  if (function.loopShape != LoopShape::None) {
    buildLoopAbstractFunction(function, config);
    return function;
  }

  switch (function.shape) {
  case Shape::StraightLine:
    initBlocks(function, 1);
    addArith(function, config, 0);
    addExtra(function, config, 0);
    break;
  case Shape::Diamond:
    initBlocks(function, 4);
    function.blocks[0].branches = true;
    function.blocks[3].merges = true;
    addArith(function, config, 1);
    addArith(function, config, 2, false, OperandKind::ArgumentB);
    addExtra(function, config, 3);
    break;
  case Shape::NestedDiamond:
    initBlocks(function, 6);
    function.blocks[0].branches = true;
    function.blocks[1].branches = true;
    function.blocks[5].merges = true;
    addArith(function, config, 2);
    addArith(function, config, 3, false, OperandKind::ArgumentB);
    addArith(function, config, 4, false, OperandKind::ArgumentB);
    addExtra(function, config, 5);
    break;
  case Shape::UnreachableTail:
    initBlocks(function, 2);
    function.blocks[1].reachable = false;
    addArith(function, config, 0);
    addExtra(function, config, 0);
    addArith(function, config, 1, true, OperandKind::ArgumentB);
    break;
  case Shape::SwitchLikeChain:
    initBlocks(function, 6);
    function.blocks[0].branches = true;
    function.blocks[2].branches = true;
    function.blocks[5].merges = true;
    addArith(function, config, 1);
    addArith(function, config, 3, false, OperandKind::ArgumentB);
    addArith(function, config, 4);
    addExtra(function, config, 5);
    break;
  }

  return function;
}

} // namespace cv
