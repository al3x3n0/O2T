// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp @ llvmorg-18.1.8
//
// This fold is the first here that reasons about ICMP and CONSTANT MASKS rather than pure boolean
// algebra, so it exercises the shim's i1 modelling, APInt mask arithmetic and ConstantInt.
//
// It also differs in SHAPE from the others: it does not receive the combined instruction, it
// receives the two icmps and a flag, and its contract is "return a Value equivalent to
// (LHS and RHS)". So the harness -- not the fold -- states that contract by building the
// conjunction. A mistake there would be mine and the fold could not catch it, which is exactly why
// the claim is also put to reference Alive2, and why the constants come from upstream's OWN worked
// example in the comment above the arm:
//     (icmp ne (A & 12), 0) & (icmp eq (A & 7), 1)  ->  (icmp eq (A & 15), 9)
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp
#include "symbolic_llvm.h"
#include <cstring>


static Value *foldLogOpOfMaskedICmps_NotAllZeros_BMask_Mixed(
    ICmpInst *LHS, ICmpInst *RHS, bool IsAnd, Value *A, Value *B, Value *C,
    Value *D, Value *E, ICmpInst::Predicate PredL, ICmpInst::Predicate PredR,
    InstCombiner::BuilderTy &Builder) {
  // We are given the canonical form:
  //   (icmp ne (A & B), 0) & (icmp eq (A & D), E).
  // where D & E == E.
  //
  // If IsAnd is false, we get it in negated form:
  //   (icmp eq (A & B), 0) | (icmp ne (A & D), E) ->
  //      !((icmp ne (A & B), 0) & (icmp eq (A & D), E)).
  //
  // We currently handle the case of B, C, D, E are constant.
  //
  const APInt *BCst, *CCst, *DCst, *OrigECst;
  if (!match(B, m_APInt(BCst)) || !match(C, m_APInt(CCst)) ||
      !match(D, m_APInt(DCst)) || !match(E, m_APInt(OrigECst)))
    return nullptr;

  ICmpInst::Predicate NewCC = IsAnd ? ICmpInst::ICMP_EQ : ICmpInst::ICMP_NE;

  // Update E to the canonical form when D is a power of two and RHS is
  // canonicalized as,
  // (icmp ne (A & D), 0) -> (icmp eq (A & D), D) or
  // (icmp ne (A & D), D) -> (icmp eq (A & D), 0).
  APInt ECst = *OrigECst;
  if (PredR != NewCC)
    ECst ^= *DCst;

  // If B or D is zero, skip because if LHS or RHS can be trivially folded by
  // other folding rules and this pattern won't apply any more.
  if (*BCst == 0 || *DCst == 0)
    return nullptr;

  // If B and D don't intersect, ie. (B & D) == 0, no folding because we can't
  // deduce anything from it.
  // For example,
  // (icmp ne (A & 12), 0) & (icmp eq (A & 3), 1) -> no folding.
  if ((*BCst & *DCst) == 0)
    return nullptr;

  // If the following two conditions are met:
  //
  // 1. mask B covers only a single bit that's not covered by mask D, that is,
  // (B & (B ^ D)) is a power of 2 (in other words, B minus the intersection of
  // B and D has only one bit set) and,
  //
  // 2. RHS (and E) indicates that the rest of B's bits are zero (in other
  // words, the intersection of B and D is zero), that is, ((B & D) & E) == 0
  //
  // then that single bit in B must be one and thus the whole expression can be
  // folded to
  //   (A & (B | D)) == (B & (B ^ D)) | E.
  //
  // For example,
  // (icmp ne (A & 12), 0) & (icmp eq (A & 7), 1) -> (icmp eq (A & 15), 9)
  // (icmp ne (A & 15), 0) & (icmp eq (A & 7), 0) -> (icmp eq (A & 15), 8)
  if ((((*BCst & *DCst) & ECst) == 0) &&
      (*BCst & (*BCst ^ *DCst)).isPowerOf2()) {
    APInt BorD = *BCst | *DCst;
    APInt BandBxorDorE = (*BCst & (*BCst ^ *DCst)) | ECst;
    Value *NewMask = ConstantInt::get(A->getType(), BorD);
    Value *NewMaskedValue = ConstantInt::get(A->getType(), BandBxorDorE);
    Value *NewAnd = Builder.CreateAnd(A, NewMask);
    return Builder.CreateICmp(NewCC, NewAnd, NewMaskedValue);
  }

  auto IsSubSetOrEqual = [](const APInt *C1, const APInt *C2) {
    return (*C1 & *C2) == *C1;
  };
  auto IsSuperSetOrEqual = [](const APInt *C1, const APInt *C2) {
    return (*C1 & *C2) == *C2;
  };

  // In the following, we consider only the cases where B is a superset of D, B
  // is a subset of D, or B == D because otherwise there's at least one bit
  // covered by B but not D, in which case we can't deduce much from it, so
  // no folding (aside from the single must-be-one bit case right above.)
  // For example,
  // (icmp ne (A & 14), 0) & (icmp eq (A & 3), 1) -> no folding.
  if (!IsSubSetOrEqual(BCst, DCst) && !IsSuperSetOrEqual(BCst, DCst))
    return nullptr;

  // At this point, either B is a superset of D, B is a subset of D or B == D.

  // If E is zero, if B is a subset of (or equal to) D, LHS and RHS contradict
  // and the whole expression becomes false (or true if negated), otherwise, no
  // folding.
  // For example,
  // (icmp ne (A & 3), 0) & (icmp eq (A & 7), 0) -> false.
  // (icmp ne (A & 15), 0) & (icmp eq (A & 3), 0) -> no folding.
  if (ECst.isZero()) {
    if (IsSubSetOrEqual(BCst, DCst))
      return ConstantInt::get(LHS->getType(), !IsAnd);
    return nullptr;
  }

  // At this point, B, D, E aren't zero and (B & D) == B, (B & D) == D or B ==
  // D. If B is a superset of (or equal to) D, since E is not zero, LHS is
  // subsumed by RHS (RHS implies LHS.) So the whole expression becomes
  // RHS. For example,
  // (icmp ne (A & 255), 0) & (icmp eq (A & 15), 8) -> (icmp eq (A & 15), 8).
  // (icmp ne (A & 15), 0) & (icmp eq (A & 15), 8) -> (icmp eq (A & 15), 8).
  if (IsSuperSetOrEqual(BCst, DCst))
    return RHS;
  // Otherwise, B is a subset of D. If B and E have a common bit set,
  // ie. (B & E) != 0, then LHS is subsumed by RHS. For example.
  // (icmp ne (A & 12), 0) & (icmp eq (A & 15), 8) -> (icmp eq (A & 15), 8).
  assert(IsSubSetOrEqual(BCst, DCst) && "Precondition due to above code");
  if ((*BCst & ECst) != 0)
    return RHS;
  // Otherwise, LHS and RHS contradict and the whole expression becomes false
  // (or true if negated.) For example,
  // (icmp ne (A & 7), 0) & (icmp eq (A & 15), 8) -> false.
  // (icmp ne (A & 6), 0) & (icmp eq (A & 15), 8) -> false.
  return ConstantInt::get(LHS->getType(), !IsAnd);
}

int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value A{"A"};
  IRBuilder Builder;
  std::string input;
  Value *out = nullptr;
  if (!strcmp(argv[1], "foldLogOpOfMaskedICmps_NotAllZeros_BMask_Mixed")) {
    Value *B  = ConstantInt::get(&CV_I32, 12ul);
    Value *C  = ConstantInt::get(&CV_I32, 0ul);
    Value *D  = ConstantInt::get(&CV_I32, 7ul);
    Value *E  = ConstantInt::get(&CV_I32, 1ul);
    Value *ab = cv_node(OP_AND, "(bvand A (_ bv12 32))", &A, B);
    Value *ad = cv_node(OP_AND, "(bvand A (_ bv7 32))",  &A, D);
    Value *LHS = Builder.CreateICmp(::ICMP_NE, ab, C);   // (A & 12) != 0
    Value *RHS = Builder.CreateICmp(::ICMP_EQ, ad, E);   // (A & 7)  == 1
    // the CALLER's contract: this fold replaces the conjunction of the two icmps
    input = "(bvand " + LHS->t + " " + RHS->t + ")";
    out = foldLogOpOfMaskedICmps_NotAllZeros_BMask_Mixed(
        LHS, RHS, /*IsAnd=*/true, &A, B, C, D, E, ::ICMP_NE, ::ICMP_EQ, Builder);
  }
  cv_emit(input, out);
  return 0;
}
