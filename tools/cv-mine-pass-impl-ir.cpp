#include "llvm/ADT/StringRef.h"
#include "llvm/Demangle/Demangle.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DebugLoc.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/DebugInfo.h"
#include "llvm/IR/DebugProgramInstruction.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InstrTypes.h"
#include "llvm/IR/Instruction.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Module.h"
#include "llvm/IRReader/IRReader.h"
#include "llvm/Config/llvm-config.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/InitLLVM.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/SourceMgr.h"
#include "llvm/Support/raw_ostream.h"

#include <map>
#include <memory>
#include <string>

namespace {

llvm::cl::OptionCategory Category("O2T pass implementation IR miner");
llvm::cl::opt<std::string> InputPath(
    "input", llvm::cl::desc("LLVM IR assembly or bitcode input"),
    llvm::cl::value_desc("file"), llvm::cl::Required,
    llvm::cl::cat(Category));

std::string valueName(const llvm::Value &Value) {
  if (Value.hasName()) {
    return Value.getName().str();
  }
  return "";
}

std::string typeString(const llvm::Type *Type) {
  if (!Type) {
    return "";
  }
  std::string Result;
  llvm::raw_string_ostream OS(Result);
  Type->print(OS);
  return OS.str();
}

std::string valueKind(const llvm::Value &Value) {
  if (llvm::isa<llvm::Instruction>(Value)) {
    return "instruction";
  }
  if (llvm::isa<llvm::Argument>(Value)) {
    return "argument";
  }
  if (llvm::isa<llvm::Function>(Value)) {
    return "function";
  }
  if (llvm::isa<llvm::Constant>(Value)) {
    return "constant";
  }
  return "other";
}

std::string debugVariableNameForAddress(const llvm::Value *Address) {
  if (!Address) {
    return "";
  }
#if LLVM_VERSION_MAJOR >= 19
  auto Declares = llvm::findDVRDeclares(const_cast<llvm::Value *>(Address));
  for (const llvm::DbgVariableRecord *Record : Declares) {
    if (!Record || !Record->isDbgDeclare() || !Record->getVariable()) {
      continue;
    }
    return Record->getVariable()->getName().str();
  }
#else
  auto Declares = llvm::findDbgDeclares(const_cast<llvm::Value *>(Address));
  for (const llvm::DbgDeclareInst *Declare : Declares) {
    if (!Declare || !Declare->getVariable()) {
      continue;
    }
    return Declare->getVariable()->getName().str();
  }
#endif
  return "";
}

std::string sourceVariableNameForValue(const llvm::Value &Value) {
  if (const auto *Load = llvm::dyn_cast<llvm::LoadInst>(&Value)) {
    if (std::string Name = debugVariableNameForAddress(Load->getPointerOperand());
        !Name.empty()) {
      return Name;
    }
  }
  if (const auto *Alloca = llvm::dyn_cast<llvm::AllocaInst>(&Value)) {
    if (std::string Name = debugVariableNameForAddress(Alloca); !Name.empty()) {
      return Name;
    }
  }
  return valueName(Value);
}

llvm::json::Object debugLocationObject(const llvm::DebugLoc &Location) {
  if (!Location) {
    return llvm::json::Object{};
  }
  const llvm::DILocation *Loc = Location.get();
  const llvm::DIScope *Scope = Loc ? Loc->getScope() : nullptr;
  std::string File;
  if (Scope) {
    llvm::StringRef Directory = Scope->getDirectory();
    llvm::StringRef Filename = Scope->getFilename();
    if (!Directory.empty()) {
      File = (Directory + "/" + Filename).str();
    } else {
      File = Filename.str();
    }
  }
  return llvm::json::Object{
      {"file", File},
      {"line", static_cast<int>(Loc ? Loc->getLine() : 0)},
      {"column", static_cast<int>(Loc ? Loc->getColumn() : 0)}};
}

llvm::json::Object functionDebugLocationObject(const llvm::Function &Function) {
  if (const llvm::DISubprogram *Subprogram = Function.getSubprogram()) {
    std::string File;
    llvm::StringRef Directory = Subprogram->getDirectory();
    llvm::StringRef Filename = Subprogram->getFilename();
    if (!Directory.empty()) {
      File = (Directory + "/" + Filename).str();
    } else {
      File = Filename.str();
    }
    return llvm::json::Object{
        {"file", File},
        {"line", static_cast<int>(Subprogram->getLine())},
        {"column", 0}};
  }
  return llvm::json::Object{};
}

std::string directCalleeName(const llvm::CallBase &Call) {
  const llvm::Function *Callee = Call.getCalledFunction();
  return Callee ? Callee->getName().str() : "";
}

} // namespace

int main(int argc, char **argv) {
  llvm::InitLLVM X(argc, argv);
  llvm::cl::HideUnrelatedOptions(Category);
  llvm::cl::ParseCommandLineOptions(argc, argv);

  llvm::LLVMContext Context;
  llvm::SMDiagnostic Diagnostic;
  std::unique_ptr<llvm::Module> Module =
      llvm::parseIRFile(InputPath, Diagnostic, Context);
  if (!Module) {
    Diagnostic.print(argv[0], llvm::errs());
    return 1;
  }

  llvm::json::Array Functions;
  llvm::json::Array BasicBlocks;
  llvm::json::Array Instructions;
  llvm::json::Array CfgEdges;
  llvm::json::Array CallEdges;
  llvm::json::Array CallArgumentEdges;
  llvm::json::Array CallOperandRefs;
  llvm::json::Array SsaEdges;

  std::map<const llvm::Instruction *, std::string> InstructionIds;
  std::map<const llvm::BasicBlock *, std::string> BlockIds;

  for (const llvm::Function &Function : Module->functions()) {
    if (Function.isDeclaration()) {
      continue;
    }
    const std::string FunctionName = Function.getName().str();
    Functions.push_back(llvm::json::Object{
        {"id", FunctionName},
        {"name", FunctionName},
        {"demangled_name", llvm::demangle(FunctionName)},
        {"debug_location", functionDebugLocationObject(Function)},
        {"basic_blocks", static_cast<int>(Function.size())}});

    unsigned BlockIndex = 0;
    unsigned InstructionIndex = 0;
    for (const llvm::BasicBlock &Block : Function) {
      const std::string BlockId =
          FunctionName + ":bb" + std::to_string(BlockIndex++);
      BlockIds[&Block] = BlockId;
      BasicBlocks.push_back(llvm::json::Object{
          {"id", BlockId},
          {"function", FunctionName},
          {"name", valueName(Block)},
          {"instructions", static_cast<int>(Block.size())}});
      for (const llvm::Instruction &Instruction : Block) {
        const std::string InstructionId =
            FunctionName + ":i" + std::to_string(InstructionIndex++);
        InstructionIds[&Instruction] = InstructionId;
      }
    }
  }

  for (const llvm::Function &Function : Module->functions()) {
    if (Function.isDeclaration()) {
      continue;
    }
    const std::string FunctionName = Function.getName().str();
    for (const llvm::BasicBlock &Block : Function) {
      const std::string BlockId = BlockIds[&Block];
      const llvm::Instruction *Terminator = Block.getTerminator();
      if (Terminator) {
        for (unsigned Index = 0; Index < Terminator->getNumSuccessors();
             ++Index) {
          const llvm::BasicBlock *Successor = Terminator->getSuccessor(Index);
          auto Found = BlockIds.find(Successor);
          if (Found != BlockIds.end()) {
            CfgEdges.push_back(llvm::json::Object{
                {"from", BlockId},
                {"to", Found->second},
                {"kind", "ir-cfg-successor"},
                {"successor_index", static_cast<int>(Index)}});
          }
        }
      }

      for (const llvm::Instruction &Instruction : Block) {
        const std::string InstructionId = InstructionIds[&Instruction];
        llvm::json::Object Object{
            {"id", InstructionId},
            {"function", FunctionName},
            {"block", BlockId},
            {"opcode", Instruction.getOpcodeName()},
            {"result", valueName(Instruction)},
            {"debug_location", debugLocationObject(Instruction.getDebugLoc())}};

        if (const auto *Call = llvm::dyn_cast<llvm::CallBase>(&Instruction)) {
          const std::string Callee = directCalleeName(*Call);
          if (!Callee.empty()) {
            Object["callee"] = Callee;
            Object["demangled_callee"] = llvm::demangle(Callee);
            CallEdges.push_back(llvm::json::Object{
                {"from", InstructionId},
                {"caller", FunctionName},
                {"callee", Callee},
                {"demangled_callee", llvm::demangle(Callee)},
                {"kind", "direct-call"},
                {"debug_location",
                 debugLocationObject(Instruction.getDebugLoc())}});
          }
          for (unsigned ArgIndex = 0; ArgIndex < Call->arg_size(); ++ArgIndex) {
            const llvm::Value *Argument = Call->getArgOperand(ArgIndex);
            CallOperandRefs.push_back(llvm::json::Object{
                {"call", InstructionId},
                {"arg_index", static_cast<int>(ArgIndex)},
                {"value_kind", valueKind(*Argument)},
                {"value_name", valueName(*Argument)},
                {"source_variable", sourceVariableNameForValue(*Argument)},
                {"value_type", typeString(Argument->getType())},
                {"callee", Callee},
                {"demangled_callee", llvm::demangle(Callee)},
                {"debug_location",
                 debugLocationObject(Instruction.getDebugLoc())}});
            const auto *ArgumentInstruction =
                llvm::dyn_cast<llvm::Instruction>(Argument);
            if (!ArgumentInstruction) {
              continue;
            }
            auto Found = InstructionIds.find(ArgumentInstruction);
            if (Found == InstructionIds.end()) {
              continue;
            }
            CallArgumentEdges.push_back(llvm::json::Object{
                {"from", Found->second},
                {"to", InstructionId},
                {"kind", "ir-call-argument"},
                {"arg_index", static_cast<int>(ArgIndex)},
                {"callee", Callee},
                {"demangled_callee", llvm::demangle(Callee)},
                {"debug_location",
                 debugLocationObject(Instruction.getDebugLoc())}});
          }
        }
        Instructions.push_back(std::move(Object));

        if (Instruction.getType()->isVoidTy()) {
          continue;
        }
        for (const llvm::User *User : Instruction.users()) {
          const auto *UserInstruction = llvm::dyn_cast<llvm::Instruction>(User);
          if (!UserInstruction) {
            continue;
          }
          auto Found = InstructionIds.find(UserInstruction);
          if (Found == InstructionIds.end()) {
            continue;
          }
          SsaEdges.push_back(llvm::json::Object{
              {"from", InstructionId},
              {"to", Found->second},
              {"kind", "ir-ssa-def-use"}});
        }
      }
    }
  }

  llvm::json::Object Output{
      {"model", "llvm-pass-impl-ir-graph-v1"},
      {"input", InputPath},
      {"module", Module->getModuleIdentifier()},
      {"analysis_precision",
       llvm::json::Object{{"cfg", "llvm-ir-basic-block-successors-v1"},
                          {"ssa_def_use", "llvm-ir-value-users-v1"},
                          {"calls", "llvm-ir-direct-callbase-v1"},
                          {"call_arguments", "llvm-ir-callbase-instruction-operands-v1"},
                          {"call_operand_refs", "llvm-ir-callbase-operands-v1"}}},
      {"functions", std::move(Functions)},
      {"basic_blocks", std::move(BasicBlocks)},
      {"instructions", std::move(Instructions)},
      {"cfg_edges", std::move(CfgEdges)},
      {"call_edges", std::move(CallEdges)},
      {"call_argument_edges", std::move(CallArgumentEdges)},
      {"call_operand_refs", std::move(CallOperandRefs)},
      {"ssa_def_use_edges", std::move(SsaEdges)}};

  llvm::outs() << llvm::formatv("{0:2}\n", llvm::json::Value(std::move(Output)));
  return 0;
}
