// A third-party pass plugin, deliberately UNSOUND, to prove O2T can verify a pass it did not build.
// It rewrites `sub x, 0` to `x` (sound) and `add x, x` to `x` (WRONG -- should be `x << 1`).
#include "llvm/IR/PassManager.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/PatternMatch.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"

using namespace llvm;
using namespace llvm::PatternMatch;

namespace {
struct ToyPass : PassInfoMixin<ToyPass> {
  PreservedAnalyses run(Function &F, FunctionAnalysisManager &) {
    bool Changed = false;
    for (auto &BB : F) {
      for (auto It = BB.begin(); It != BB.end();) {
        Instruction *I = &*It++;
        Value *X = nullptr;
        // SOUND: sub x, 0 -> x
        if (match(I, m_Sub(m_Value(X), m_Zero()))) {
          I->replaceAllUsesWith(X);
          I->eraseFromParent();
          Changed = true;
          continue;
        }
        // UNSOUND (planted): add x, x -> x   (correct answer is x*2)
        if (match(I, m_Add(m_Value(X), m_Deferred(X)))) {
          I->replaceAllUsesWith(X);
          I->eraseFromParent();
          Changed = true;
        }
      }
    }
    return Changed ? PreservedAnalyses::none() : PreservedAnalyses::all();
  }
  static bool isRequired() { return true; }
};
} // namespace

extern "C" ::llvm::PassPluginLibraryInfo LLVM_ATTRIBUTE_WEAK llvmGetPassPluginInfo() {
  return {LLVM_PLUGIN_API_VERSION, "ToyPass", "0.1", [](PassBuilder &PB) {
    PB.registerPipelineParsingCallback(
        [](StringRef Name, FunctionPassManager &FPM, ArrayRef<PassBuilder::PipelineElement>) {
          if (Name == "toypass") { FPM.addPass(ToyPass()); return true; }
          return false;
        });
  }};
}
