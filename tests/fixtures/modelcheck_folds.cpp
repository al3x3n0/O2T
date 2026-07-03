// Bounded-model-checker fixtures for real LLVM-style fold verification.
//
// Each check_* function nondeterministically picks inputs, runs a fold written against the
// modelcheck LLVM shim, and asserts poison-aware refinement only if the fold rewrites.
#include "modelcheck_llvm.h"

static Value foldURemGuarded(Value X, Value P, IRBuilder &B) {
  if (isKnownToBeAPowerOfTwo(P))
    return B.CreateAnd(X, B.CreateSub(P, ConstantInt::get(1)));
  return cv_no_rewrite();
}

static Value foldURemUnguarded(Value X, Value P, IRBuilder &B) {
  return B.CreateAnd(X, B.CreateSub(P, ConstantInt::get(1)));
}

static Value foldAddNSWGuarded(Value X, Value Y, IRBuilder &B) {
  if (willNotOverflowSignedAdd(X, Y))
    return B.CreateNSWAdd(X, Y);
  return cv_no_rewrite();
}

static Value foldAddNSWUnguarded(Value X, Value Y, IRBuilder &B) {
  return B.CreateNSWAdd(X, Y);
}

static Value foldSelectToOrRaw(Value C, Value Y, IRBuilder &B) {
  return B.CreateOrPoisoning(C, Y);
}

static Value foldSelectToOrFreeze(Value C, Value Y, IRBuilder &B) {
  return B.CreateOrPoisoning(C, B.CreateFreeze(Y));
}

extern "C" void check_urem_guarded() {
  IRBuilder B;
  Value X = cv_any_i32();
  Value P = cv_any_i32();
  Value input = B.CreateURem(X, P);
  cv_assert_refines(input, foldURemGuarded(X, P, B), "urem guarded refinement");
}

extern "C" void check_urem_unguarded() {
  IRBuilder B;
  Value X = cv_any_i32();
  Value P = cv_any_i32();
  Value input = B.CreateURem(X, P);
  cv_assert_refines(input, foldURemUnguarded(X, P, B), "urem unguarded refinement");
}

extern "C" void check_add_nsw_guarded() {
  IRBuilder B;
  Value X = cv_any_i32();
  Value Y = cv_any_i32();
  Value input = B.CreateAdd(X, Y);
  cv_assert_refines(input, foldAddNSWGuarded(X, Y, B), "add nsw guarded refinement");
}

extern "C" void check_add_nsw_unguarded() {
  IRBuilder B;
  Value X = cv_any_i32();
  Value Y = cv_any_i32();
  Value input = B.CreateAdd(X, Y);
  cv_assert_refines(input, foldAddNSWUnguarded(X, Y, B), "add nsw unguarded refinement");
}

extern "C" void check_select_to_or_raw() {
  IRBuilder B;
  Value C = cv_any_i1();
  Value Y = cv_any_poison_i1();
  Value input = B.CreateSelect(C, ConstantInt::get(1), Y);
  cv_assert_refines(input, foldSelectToOrRaw(C, Y, B), "select to raw or refinement");
}

extern "C" void check_select_to_or_freeze() {
  IRBuilder B;
  Value C = cv_any_i1();
  Value Y = cv_any_poison_i1();
  Value input = B.CreateSelect(C, ConstantInt::get(1), Y);
  cv_assert_refines(input, foldSelectToOrFreeze(C, Y, B), "select to frozen or refinement");
}

int main() {
  return 0;
}
