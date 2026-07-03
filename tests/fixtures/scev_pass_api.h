//===-- Minimal SCEV API surface for AST-based pass-source mining ----------===//
// cv-mine-clang-pass.py -include's this so libclang resolves the getMulExpr/
// getAddRecExpr/getConstant member calls into a clean, typed AST. It is NOT the
// real LLVM header -- just enough signatures for the recognizer to read the call
// structure of curated pass-source excerpts. Mirrors how the .ll path shells out
// to `opt` instead of reimplementing LLVM.
//===----------------------------------------------------------------------===//
#ifndef CV_SCEV_PASS_API_H
#define CV_SCEV_PASS_API_H
namespace llvm {
struct Loop;
struct SCEV {
  enum NoWrapFlags { FlagAnyWrap = 0, FlagNUW = 1, FlagNSW = 2 };
};
struct ScalarEvolution {
  const SCEV *getConstant(int);
  const SCEV *getConstant(const SCEV *);
  const SCEV *getMulExpr(const SCEV *, const SCEV *);
  const SCEV *getAddExpr(const SCEV *, const SCEV *);
  const SCEV *getAddRecExpr(const SCEV *, const SCEV *, const Loop *, int = 0);
};
const SCEV *rewriteUses(const SCEV *, const SCEV *);
}  // namespace llvm
#endif
