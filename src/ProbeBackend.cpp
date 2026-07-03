#include "o2t/ProbeBackend.h"

#include "o2t/IRTextGenerator.h"
#include "o2t/PassInstrumentation.h"

#include <memory>
#include <string>

#if (defined(O2T_WITH_LLVM) || defined(COMPILERVERIF_WITH_LLVM))
#include <llvm/Analysis/CGSCCPassManager.h>
#include <llvm/Analysis/LoopAnalysisManager.h>
#include <llvm/AsmParser/Parser.h>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/PassManager.h>
#include <llvm/IR/Verifier.h>
#include <llvm/Passes/PassBuilder.h>
#include <llvm/Support/Error.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/Support/raw_ostream.h>
#endif

namespace cv {

ProbeBackendResult runLLVMProbeBackend(const GeneratorConfig &rawConfig) {
  ProbeBackendResult result;
  result.kind = ProbeBackendKind::LLVM;
  result.coverage = scanOptimizationProbes(buildAbstractFunction(rawConfig));

#if !(defined(O2T_WITH_LLVM) || defined(COMPILERVERIF_WITH_LLVM))
  result.available = false;
  return result;
#else
  const GeneratorConfig config = normalizeConfig(rawConfig);
  const GeneratedIR generated = generateIR(config);

  llvm::LLVMContext context;
  llvm::SMDiagnostic diagnostic;
  std::unique_ptr<llvm::Module> module =
      llvm::parseAssemblyString(generated.moduleText, diagnostic, context);
  if (!module) {
    result.available = false;
    return result;
  }

  if (llvm::verifyModule(*module, &llvm::errs())) {
    result.available = false;
    return result;
  }

  llvm::LoopAnalysisManager loopAnalyses;
  llvm::FunctionAnalysisManager functionAnalyses;
  llvm::CGSCCAnalysisManager cgsccAnalyses;
  llvm::ModuleAnalysisManager moduleAnalyses;
  llvm::PassBuilder passBuilder;

  passBuilder.registerModuleAnalyses(moduleAnalyses);
  passBuilder.registerCGSCCAnalyses(cgsccAnalyses);
  passBuilder.registerFunctionAnalyses(functionAnalyses);
  passBuilder.registerLoopAnalyses(loopAnalyses);
  passBuilder.crossRegisterProxies(loopAnalyses, functionAnalyses, cgsccAnalyses,
                                   moduleAnalyses);

  llvm::ModulePassManager modulePasses;
  llvm::Error error =
      passBuilder.parsePassPipeline(modulePasses, probePipelineForConfig(config));
  if (error) {
    llvm::consumeError(std::move(error));
    result.available = false;
    return result;
  }

  clearPassProbeEvents();
  modulePasses.run(*module, moduleAnalyses);
  result.observedMarkers = passProbeEvents();
  result.available = !llvm::verifyModule(*module, &llvm::errs());
  return result;
#endif
}

} // namespace cv
