// UNMODIFIED upstream LLVM 18 InstCombine source, vendored byte-for-byte from
//   llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp @ llvmorg-18.1.8
//
//   Cond ? (X & ~C) : (X | C)  ->  (X & ~C) | (Cond ? 0 : C)
//   Cond ? (X | C)  : (X & ~C) ->  (X & ~C) | (Cond ? C : 0)
//
// A select between "clear these bits" and "set these bits" becomes one masked value or'd with a
// select between two CONSTANTS -- the select stops choosing between two computations and chooses
// between 0 and C instead.
//
// What makes it interesting here is the guard `*NotC == ~(*C)`: the two constants must be exact
// complements, and that is a statement about the CONSTANTS at their width. This is the first
// vendored fold to depend on `APInt::operator~` agreeing with the width it is applied at, which is
// the same property `isAllOnes` was getting wrong until this batch (it compared against a hard-coded
// 0xFFFFFFFF, so an i8 all-ones answered no).
//
// It is also the first to build a select through the IRBuilder rather than only to match one, and
// upstream passes a name and a metadata source in the same call -- arity that carries no semantics
// but that the fold does not compile without.
//
// Reproduce the vendoring with:
//   curl -fsSLO https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-18.1.8/llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp
#include "symbolic_llvm.h"
#include <cstring>

static Instruction *foldSetClearBits(SelectInst &Sel,
                                     InstCombiner::BuilderTy &Builder) {
  Value *Cond = Sel.getCondition();
  Value *T = Sel.getTrueValue();
  Value *F = Sel.getFalseValue();
  Type *Ty = Sel.getType();
  Value *X;
  const APInt *NotC, *C;

  // Cond ? (X & ~C) : (X | C) --> (X & ~C) | (Cond ? 0 : C)
  if (match(T, m_And(m_Value(X), m_APInt(NotC))) &&
      match(F, m_OneUse(m_Or(m_Specific(X), m_APInt(C)))) && *NotC == ~(*C)) {
    Constant *Zero = ConstantInt::getNullValue(Ty);
    Constant *OrC = ConstantInt::get(Ty, *C);
    Value *NewSel = Builder.CreateSelect(Cond, Zero, OrC, "masksel", &Sel);
    return BinaryOperator::CreateOr(T, NewSel);
  }

  // Cond ? (X | C) : (X & ~C) --> (X & ~C) | (Cond ? C : 0)
  if (match(F, m_And(m_Value(X), m_APInt(NotC))) &&
      match(T, m_OneUse(m_Or(m_Specific(X), m_APInt(C)))) && *NotC == ~(*C)) {
    Constant *Zero = ConstantInt::getNullValue(Ty);
    Constant *OrC = ConstantInt::get(Ty, *C);
    Value *NewSel = Builder.CreateSelect(Cond, OrC, Zero, "masksel", &Sel);
    return BinaryOperator::CreateOr(F, NewSel);
  }

  return nullptr;
}

// ---------------------------------------------------------------------------------------------
// The harness builds the select the fold receives. Both arms are exercised: the difference between
// them is only WHICH side carries the mask, and a copy-paste slip between the two (selecting C where
// 0 belongs) is the most plausible real bug in a fold shaped like this.
int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  IRBuilder Builder;
  Value X{"X"};
  // The selector is a COMPARISON rather than a bare i1 variable. Both are legal IR and the fold
  // treats the condition opaquely, so nothing about the theorem changes -- `Y != 0` ranges over both
  // i1 values as Y does. But a bare i1 free variable is untypeable when the terms are rendered back
  // to IR for the Alive2 cross-check (the renderer gives an unknown variable the default width), and
  // an arm that cannot be independently confirmed is not one to claim.
  Value Cond{cv_icmp_term(::ICMP_NE, "Y", "(_ bv0 32)")};
  Cond.ty = cv_i1();
  std::string input;
  Value *out = nullptr;
  const char *f = argv[1];

  const unsigned long CMASK = 12ul, NOTC = 0xFFFFFFF3ul;   // exact complements at i32
  Value *Cc = ConstantInt::get(&CV_I32, CMASK);
  Value *NotCc = ConstantInt::get(&CV_I32, NOTC);
  std::string andt = "(bvand X " + NotCc->t + ")", ort = "(bvor X " + Cc->t + ")";
  Value *And = cv_node(OP_AND, andt.c_str(), &X, NotCc);
  Value *Or = cv_node(OP_OR, ort.c_str(), &X, Cc);

  bool clear_first = !strcmp(f, "setclear_clear_first");   // Cond ? (X & ~C) : (X | C)
  bool set_first = !strcmp(f, "setclear_set_first");       // Cond ? (X | C)  : (X & ~C)
  if (clear_first || set_first) {
    Value *T = clear_first ? And : Or;
    Value *F = clear_first ? Or : And;
    Value Sel{"(ite (= " + Cond.t + " #b1) " + T->t + " " + F->t + ")"};
    Sel.cond = &Cond; Sel.tval = T; Sel.fval = F;
    input = Sel.t;
    out = foldSetClearBits(Sel, Builder);
  }
  cv_emit(input, out);
  return 0;
}
