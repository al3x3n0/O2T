// Real InstCombine-style folds, written against the symbolic-LLVM shim so O2T can symbolically
// execute the ACTUAL C++ (its real branches) over a symbolic input and discharge soundness per
// control-flow path. One fold is correctly guarded; one is planted under-guarded (it rewrites
// without checking the precondition its rewrite needs).
#include "symbolic_llvm.h"
#include <cstring>
#include <vector>

// ---- a real multi-instruction WORKLIST pass (not a single fold) -------------------------------
// Models the InstCombine-style structure: iterate a straight-line block to FIXPOINT, replacing each
// instruction by its simplified form and threading that substitution into its users (operands are
// referenced by index, so a simplified node is picked up when its users are re-evaluated). O2T then
// discharges that the COMPOSED final value refines the original block -- verifying the whole pass run.
enum WOp { W_LEAF, W_ADD, W_MUL, W_SUB };
struct WNode { WOp op; int a; bool immB; unsigned long b; std::string lit; };

static std::string wImm(unsigned long v) { char s[40]; snprintf(s, sizeof s, "(_ bv%lu 32)", v); return s; }
static std::string wRaw(WOp op, const std::string &a, const std::string &b) {
  if (op == W_ADD) return "(bvadd " + a + " " + b + ")";
  if (op == W_MUL) return "(bvmul " + a + " " + b + ")";
  if (op == W_SUB) return "(bvsub " + a + " " + b + ")";
  return a;
}
// the per-instruction simplifications the pass knows. `buggy` plants an unsound sub-self rule.
static bool wSimplify(WOp op, const std::string &a, const std::string &b, bool buggy, std::string &out) {
  if (op == W_ADD && b == "(_ bv0 32)") { out = a; return true; }            // v + 0 -> v
  if (op == W_MUL && b == "(_ bv1 32)") { out = a; return true; }            // v * 1 -> v
  if (op == W_SUB && a == b) { out = buggy ? a : "(_ bv0 32)"; return true; } // v - v -> 0 (buggy: -> v)
  return false;
}
static std::string wOperand(std::vector<WNode> &n, std::vector<std::string> &cur, int i) {
  return cur[i];
}
// original (unsimplified) semantics of node i -- the spec the pass output must refine.
static std::string wOrig(std::vector<WNode> &n, int i) {
  if (n[i].op == W_LEAF) return n[i].lit;
  std::string a = wOrig(n, n[i].a), b = n[i].immB ? wImm(n[i].b) : wOrig(n, (int)n[i].b);
  return wRaw(n[i].op, a, b);
}
// run the worklist to fixpoint; return the final term of the root (last) node.
static std::string wRun(std::vector<WNode> &n, bool buggy) {
  std::vector<std::string> cur(n.size());
  for (size_t i = 0; i < n.size(); i++) if (n[i].op == W_LEAF) cur[i] = n[i].lit;
  bool changed = true;
  while (changed) {
    changed = false;
    for (size_t i = 0; i < n.size(); i++) {
      if (n[i].op == W_LEAF) continue;
      std::string a = wOperand(n, cur, n[i].a);
      std::string b = n[i].immB ? wImm(n[i].b) : wOperand(n, cur, (int)n[i].b);
      std::string s, nc = wSimplify(n[i].op, a, b, buggy, s) ? s : wRaw(n[i].op, a, b);
      if (nc != cur[i]) { cur[i] = nc; changed = true; }
    }
  }
  return cur.back();
}

// urem X, P  ->  X & (P-1)   -- SOUND only when P is a power of two. Correctly guarded.
static Value foldURemGuarded(Value X, Value P, IRBuilder &B) {
  if (isKnownToBeAPowerOfTwo(P))
    return B.CreateAnd(X, B.CreateSub(P, ConstantInt::get(1)));
  return Value{""};                              // no fold
}

// urem X, P  ->  X & (P-1)   -- PLANTED BUG: rewrites unconditionally (no power-of-two guard).
static Value foldURemUnguarded(Value X, Value P, IRBuilder &B) {
  return B.CreateAnd(X, B.CreateSub(P, ConstantInt::get(1)));
}

// sdiv X, P -> udiv X, P  -- SOUND only when X is known non-negative. Correctly guarded.
static Value foldSDivGuarded(Value X, Value P, IRBuilder &B) {
  if (isKnownNonNegative(X) && isKnownNonNegative(P))
    return B.CreateUDiv(X, P);
  return Value{""};
}

// Folds written the way REAL 3rd-party InstCombine code is -- PatternMatch on the instruction
// (incl. NESTED patterns and m_Specific), capture operands, optional precondition query, IRBuilder.
static Value foldURemPattern(Instruction &I, IRBuilder &B) {
  Value X, Y;
  if (match(I, m_URem(m_Value(X), m_Value(Y))))         // match urem, capture X, Y
    if (isKnownToBeAPowerOfTwo(Y))                       // the precondition the rewrite needs
      return B.CreateAnd(X, B.CreateSub(Y, ConstantInt::get(1)));
  return Value{""};
}

// x - x  ->  0   (m_Specific: the second operand is the SAME value captured as the first)
static Value foldSubSelf(Instruction &I, IRBuilder &) {
  Value X;
  if (match(I, m_Sub(m_Value(X), m_Specific(X))))
    return ConstantInt::get(0);
  return Value{""};
}

// (a + b) - b  ->  a   (a NESTED pattern: match an add inside a sub, with m_Specific on b)
static Value foldAddSubCancel(Instruction &I, IRBuilder &) {
  Value A, Bv;
  if (match(I, m_Sub(m_Add(m_Value(A), m_Value(Bv)), m_Specific(Bv))))
    return A;
  return Value{""};
}

// x & -1  ->  x   (a constant matcher: the second operand is the all-ones literal)
static Value foldAndAllOnes(Instruction &I, IRBuilder &) {
  Value X;
  if (match(I, m_And(m_Value(X), m_AllOnes())))
    return X;
  return Value{""};
}

// mul X, C  ->  shl X, log2(C)   when C is a power of two -- captures the CONSTANT and reasons
// about an APInt-derived value (logBase2). Real strength-reduction idiom: m_ConstantInt capture +
// APInt method + IRBuilder, with the power-of-two precondition.
static Value foldMulPow2(Instruction &I, IRBuilder &B) {
  Value X, C;
  if (match(I, m_Mul(m_Value(X), m_ConstantInt(C))))   // capture X and the constant C
    if (isKnownToBeAPowerOfTwo(C))                       // C must be a power of two
      return B.CreateShl(X, cv_logBase2(C));            // shift by the derived exponent
  return Value{""};
}

// x + 0 -> x   (uses dyn_cast<BinaryOperator> + isa + m_Zero -- common dispatch idioms)
static Value foldAddZero(Instruction &I, IRBuilder &) {
  if (const Value *BO = dyn_cast<BinaryOperator>(I)) {   // only dispatch on a binary operator
    Value X;
    if (match(*BO, m_Add(m_Value(X), m_Zero())))
      return X;
  }
  return Value{""};
}

// 0 + x -> x   (m_c_Add: COMMUTATIVE match, so the zero may be on either side -- the way real
// InstCombine code matches with m_c_*). Verified on the operand-reversed input `add(0, X)`.
static Value foldAddZeroComm(Instruction &I, IRBuilder &) {
  Value X;
  if (match(I, m_c_Add(m_Value(X), m_Zero())))
    return X;
  return Value{""};
}

// PLANTED BUG: mul X, C -> shl X, log2(C) WITHOUT the power-of-two guard. For a non-power-of-two C
// the shift loses the high product bits, so this is refuted (the guard is load-bearing).
static Value foldMulPow2Unguarded(Instruction &I, IRBuilder &B) {
  Value X, C;
  if (match(I, m_Mul(m_Value(X), m_ConstantInt(C))))
    return B.CreateShl(X, cv_logBase2(C));
  return Value{""};
}

// add X, Y  ->  add nsw X, Y   -- setting the nsw flag. SOUND only when the add cannot signed-
// overflow; otherwise the rewrite INTRODUCES poison the source did not have (a refinement bug).
static Value foldAddNSWGuarded(Instruction &I, IRBuilder &B) {
  Value X, Y;
  if (match(I, m_Add(m_Value(X), m_Value(Y))))
    if (willNotOverflowSignedAdd(X, Y))            // prove no signed overflow before setting nsw
      return B.CreateNSWAdd(X, Y);
  return Value{""};
}
// PLANTED BUG: sets nsw unconditionally -> poison on overflow -> does NOT refine the source.
static Value foldAddNSWUnguarded(Instruction &I, IRBuilder &B) {
  Value X, Y;
  if (match(I, m_Add(m_Value(X), m_Value(Y))))
    return B.CreateNSWAdd(X, Y);
  return Value{""};
}

// add X,Y -> add nuw X,Y  -- sound only when the add cannot UNSIGNED-overflow.
static Value foldAddNUWGuarded(Instruction &I, IRBuilder &B) {
  Value X, Y;
  if (match(I, m_Add(m_Value(X), m_Value(Y))))
    if (willNotOverflowUnsignedAdd(X, Y))
      return B.CreateNUWAdd(X, Y);
  return Value{""};
}
static Value foldAddNUWUnguarded(Instruction &I, IRBuilder &B) {   // BUG: nuw with no guard
  Value X, Y;
  if (match(I, m_Add(m_Value(X), m_Value(Y))))
    return B.CreateNUWAdd(X, Y);
  return Value{""};
}

// add X,Y -> or disjoint X,Y  -- the VALUE is equal only when X&Y==0, and the `disjoint` flag is
// poison unless X&Y==0; the single haveNoCommonBitsSet fact discharges both. Guarded is sound;
// unguarded both miscomputes the value AND sets a false flag (refuted on any X,Y with common bits).
static Value foldAddToOrDisjointGuarded(Instruction &I, IRBuilder &B) {
  Value X, Y;
  if (match(I, m_Add(m_Value(X), m_Value(Y))))
    if (haveNoCommonBitsSet(X, Y))
      return B.CreateOrDisjoint(X, Y);
  return Value{""};
}
static Value foldAddToOrDisjointUnguarded(Instruction &I, IRBuilder &B) {   // BUG: no disjointness guard
  Value X, Y;
  if (match(I, m_Add(m_Value(X), m_Value(Y))))
    return B.CreateOrDisjoint(X, Y);
  return Value{""};
}

// udiv X,Y -> udiv exact X,Y  -- sound only when Y divides X (poison depends on operand VALUES).
static Value foldUDivExactGuarded(Instruction &I, IRBuilder &B) {
  Value X, Y;
  if (match(I, m_UDiv(m_Value(X), m_Value(Y))))
    if (isKnownExactUDiv(X, Y))
      return B.CreateExactUDiv(X, Y);
  return Value{""};
}
static Value foldUDivExactUnguarded(Instruction &I, IRBuilder &B) {   // BUG: exact with no remainder guard
  Value X, Y;
  if (match(I, m_UDiv(m_Value(X), m_Value(Y))))
    return B.CreateExactUDiv(X, Y);
  return Value{""};
}

// add nsw (add nsw X, C1), C2  ->  add nsw X, (C1 + C2)   -- combine two nsw adds of constants.
// The VALUE is X+C1+C2 either way (equal mod 2^32), so value-equality proves it sound. But the
// single combined `add nsw X, (C1+C2)` is poison when C1+C2 itself signed-overflows, even on inputs
// where the source (both inner adds in range) was defined. SOUND only when C1+C2 cannot overflow.
static Value foldNestedNSWAddConst(Instruction &I, IRBuilder &B, bool guarded) {
  Value X, C1, C2;
  if (match(I, m_Add(m_Add(m_Value(X), m_ConstantInt(C1)), m_ConstantInt(C2)))) {
    if (guarded && !willNotOverflowSignedAdd(C1, C2))
      return Value{""};                            // can't prove the combined constant is in range
    return B.CreateNSWAdd(X, B.CreateAdd(C1, C2));  // add nsw X, (C1+C2)
  }
  return Value{""};
}

// select C, true, Y  ->  or C, Y    ("logical or" simplification). The VALUE is identical on i1
// (C?1:Y  ==  C|Y), so value-equality proves it sound. But it is POISON-UNSOUND: `or C, Y` is poison
// whenever Y is poison, while the source select returns 1 (defined) when C is true regardless of Y.
// This is the canonical reason `freeze` exists. The `freeze` variant (-> or C, freeze Y) is sound.
static Value foldSelectTrueOr(Value C, Value Y, IRBuilder &B, bool freeze) {
  return B.CreateOrPoisoning(C, freeze ? B.CreateFreeze(Y) : Y);
}

// fadd X, Y  ->  fadd nnan X, Y   -- setting the fast-math nnan flag. The FP analogue of nsw: sound
// only when the sum cannot be NaN; otherwise the flag introduces poison the source did not have
// (e.g. +inf + -inf is a defined NaN in the source, poison under nnan).
static Value foldFAddNNan(Value X, Value Y, IRBuilder &B, bool guarded) {
  if (guarded && !willNotBeNaN(X, Y))
    return Value{""};
  return B.CreateFAddNNan(X, Y);
}

// store V, P ; X = load Q  ->  X = V    (store-to-load forwarding). Sound ONLY when P and Q must
// alias (same address); forwarding across a possibly-different pointer returns the wrong value.
// Modeled in the array theory: the loaded value is select(store(MEM,P,V), Q), the forward yields V.
static Value foldStoreToLoadForward(Value StoredVal, Value StorePtr, Value LoadPtr, bool guarded) {
  if (guarded && !isMustAlias(StorePtr, LoadPtr))
    return Value{""};                              // can't prove the load reads the just-stored cell
  return StoredVal;                                // forward the stored value to the load
}

// Dead-store elimination: `store V,P ; X = load Q` with the store removed (it is dead w.r.t. a later
// overwrite). Removing it changes the intervening load UNLESS P and Q do not alias: after removal the
// load reads the ORIGINAL memory select(MEM,Q) instead of the stored V. Sound only under no-alias.
static Value foldDeadStoreElim(Value StorePtr, Value LoadPtr, Value Mem, bool guarded) {
  if (guarded && !isNoAlias(StorePtr, LoadPtr))
    return Value{""};                              // the load may read the removed store's cell
  Value r; r.t = "(select " + Mem.t + " " + LoadPtr.t + ")"; return r;  // load now reads original mem
}

// mul (lshr X, 1), 2  ->  X   -- a MULTI-INSTRUCTION peephole: it matches on the operand's PRODUCER
// (the lshr feeding the mul). It looks like a shift round-trip, but (X>>1)<<1 drops X's low bit, so
// it equals X only when X is even. Sound only under the known-bits fact that X's low bit is zero.
static Value foldShrShlRoundtrip(Instruction &I, IRBuilder &, bool guarded) {
  Value X;
  if (match(I, m_Mul(m_LShr(m_Value(X), m_One()), m_SpecificInt(2)))) {
    if (guarded && !isLowBitZero(X))
      return Value{""};                            // can't drop a low bit that might be set
    return X;
  }
  return Value{""};
}

// Speculate a guarded load: `if (i < n) v = load a[i] else v = 0`  ->  `v = load a[i]` (hoist the
// load out of its bounds guard). UNSOUND: when i >= n the speculated load is out-of-bounds -- UB the
// guarded source never had. Sound only when the index is provably in bounds.
static Value foldSpeculateLoad(Value Idx, Value Size, Value Mem, bool guarded) {
  if (guarded && !isInBounds(Idx, Size))
    return Value{""};                              // can't prove the speculated access is in bounds
  Value r; r.t = "(select " + Mem.t + " " + Idx.t + ")";
  r.poison = "(bvuge " + Idx.t + " " + Size.t + ")";  // an out-of-bounds load is UB (modeled as poison)
  return r;
}

int main(int argc, char **argv) {
  if (argc < 2) return 1;
  cv_setup(argc, argv);
  Value X{"X"}, P{"P"};
  IRBuilder B;
  Value out{""};
  std::string input;
  const char *f = argv[1];
  if (!strcmp(f, "urem_guarded"))   { input = "(bvurem X P)"; out = foldURemGuarded(X, P, B); }
  else if (!strcmp(f, "urem_unguarded")) { input = "(bvurem X P)"; out = foldURemUnguarded(X, P, B); }
  else if (!strcmp(f, "sdiv_guarded")) { input = "(bvsdiv X P)"; out = foldSDivGuarded(X, P, B); }
  else if (!strcmp(f, "urem_pattern")) {              // match + capture + guard
    Instruction I = *cv_node(OP_UREM, "(bvurem X P)", &X, &P);
    input = "(bvurem X P)";
    out = foldURemPattern(I, B);
  }
  else if (!strcmp(f, "sub_self")) {                  // x - x -> 0  (m_Specific)
    Instruction I = *cv_node(OP_SUB, "(bvsub X X)", &X, &X);
    input = "(bvsub X X)";
    out = foldSubSelf(I, B);
  }
  else if (!strcmp(f, "add_sub_cancel")) {            // (A + B) - B -> A  (nested pattern)
    Value A{"A"}, Bv{"B"};
    Value *inner = cv_node(OP_ADD, "(bvadd A B)", &A, &Bv);
    Instruction I = *cv_node(OP_SUB, "(bvsub (bvadd A B) B)", inner, &Bv);
    input = "(bvsub (bvadd A B) B)";
    out = foldAddSubCancel(I, B);
  }
  else if (!strcmp(f, "and_allones")) {               // X & -1 -> X  (constant matcher)
    Value ones{"(_ bv4294967295 32)"};
    Instruction I = *cv_node(OP_AND, "(bvand X (_ bv4294967295 32))", &X, &ones);
    input = "(bvand X (_ bv4294967295 32))";
    out = foldAndAllOnes(I, B);
  }
  else if (!strcmp(f, "mul_pow2")) {                  // mul X, C -> shl X, log2(C)  (captured const)
    Value C{"C"}; C.is_const = true;                  // a symbolic ConstantInt operand
    Instruction I = *cv_node(OP_MUL, "(bvmul X C)", &X, &C);
    input = "(bvmul X C)";
    out = foldMulPow2(I, B);
  }
  else if (!strcmp(f, "mul_pow2_unguarded")) {        // BUG: same fold, no power-of-two guard
    Value C{"C"}; C.is_const = true;
    Instruction I = *cv_node(OP_MUL, "(bvmul X C)", &X, &C);
    input = "(bvmul X C)";
    out = foldMulPow2Unguarded(I, B);
  }
  else if (!strcmp(f, "add_zero")) {                  // x + 0 -> x  (dyn_cast + m_Zero)
    Value zero{"(_ bv0 32)"}; zero.is_const = true;
    Instruction I = *cv_node(OP_ADD, "(bvadd X (_ bv0 32))", &X, &zero);
    input = "(bvadd X (_ bv0 32))";
    out = foldAddZero(I, B);
  }
  else if (!strcmp(f, "add_zero_comm")) {             // 0 + X -> X  (commutative, zero on the LEFT)
    Value zero{"(_ bv0 32)"}; zero.is_const = true;
    Instruction I = *cv_node(OP_ADD, "(bvadd (_ bv0 32) X)", &zero, &X);
    input = "(bvadd (_ bv0 32) X)";
    out = foldAddZeroComm(I, B);
  }
  else if (!strcmp(f, "add_nsw_guarded")) {          // add X,Y -> add nsw X,Y  (guarded by overflow query)
    Value Y{"Y"};
    Instruction I = *cv_node(OP_ADD, "(bvadd X Y)", &X, &Y);
    input = "(bvadd X Y)";                            // source add: defined for all X,Y (input_poison=false)
    out = foldAddNSWGuarded(I, B);
  }
  else if (!strcmp(f, "add_nsw_unguarded")) {        // BUG: sets nsw with no overflow guard -> poison on overflow
    Value Y{"Y"};
    Instruction I = *cv_node(OP_ADD, "(bvadd X Y)", &X, &Y);
    input = "(bvadd X Y)";
    out = foldAddNSWUnguarded(I, B);
  }
  else if (!strcmp(f, "add_nuw_guarded")) {          // add X,Y -> add nuw X,Y  (unsigned-overflow guard)
    Value Y{"Y"};
    Instruction I = *cv_node(OP_ADD, "(bvadd X Y)", &X, &Y);
    input = "(bvadd X Y)";
    out = foldAddNUWGuarded(I, B);
  }
  else if (!strcmp(f, "add_nuw_unguarded")) {        // BUG: nuw with no guard
    Value Y{"Y"};
    Instruction I = *cv_node(OP_ADD, "(bvadd X Y)", &X, &Y);
    input = "(bvadd X Y)";
    out = foldAddNUWUnguarded(I, B);
  }
  else if (!strcmp(f, "add_or_disjoint_guarded")) {  // add X,Y -> or disjoint X,Y  (no-common-bits guard)
    Value Y{"Y"};
    Instruction I = *cv_node(OP_ADD, "(bvadd X Y)", &X, &Y);
    input = "(bvadd X Y)";
    out = foldAddToOrDisjointGuarded(I, B);
  }
  else if (!strcmp(f, "add_or_disjoint_unguarded")) {// BUG: value wrong AND false flag when X&Y!=0
    Value Y{"Y"};
    Instruction I = *cv_node(OP_ADD, "(bvadd X Y)", &X, &Y);
    input = "(bvadd X Y)";
    out = foldAddToOrDisjointUnguarded(I, B);
  }
  else if (!strcmp(f, "udiv_exact_guarded")) {       // udiv X,Y -> udiv exact X,Y  (divides-evenly guard)
    Value Y{"Y"};
    Instruction I = *cv_node(OP_UDIV, "(bvudiv X Y)", &X, &Y);
    input = "(bvudiv X Y)";
    out = foldUDivExactGuarded(I, B);
  }
  else if (!strcmp(f, "udiv_exact_unguarded")) {     // BUG: exact with no remainder guard
    Value Y{"Y"};
    Instruction I = *cv_node(OP_UDIV, "(bvudiv X Y)", &X, &Y);
    input = "(bvudiv X Y)";
    out = foldUDivExactUnguarded(I, B);
  }
  else if (!strncmp(f, "nested_nsw_addconst", 19)) {  // combine two nsw adds: add nsw(add nsw X,C1),C2
    bool guarded = !strcmp(f, "nested_nsw_addconst_guarded");
    Value C1{"C1"}, C2{"C2"}; C1.is_const = true; C2.is_const = true;
    Value *inner = cv_node(OP_ADD, "(bvadd X C1)", &X, &C1);
    Instruction I = *cv_node(OP_ADD, "(bvadd (bvadd X C1) C2)", inner, &C2);
    input = "(bvadd (bvadd X C1) C2)";
    // the SOURCE is itself flagged: poison when EITHER nsw add overflows (inner X+C1, or outer +C2).
    CV_INPUT_POISON = "(or " + cv_saddo("X", "C1") + " " + cv_saddo("(bvadd X C1)", "C2") + ")";
    out = foldNestedNSWAddConst(I, B, guarded);
  }
  else if (!strncmp(f, "select_to_or", 12)) {        // select C,true,Y -> or C,Y  (poison contagion)
    bool freeze = !strcmp(f, "select_to_or_freeze");
    Value C{"S"}; C.poison = "Sp";                    // i1 selector, may itself be poison
    Value W{"W"}; W.poison = "Wp";                    // i1 operand, may itself be poison
    cv_decl("(declare-const S (_ BitVec 1))");
    cv_decl("(declare-const W (_ BitVec 1))");
    cv_decl("(declare-const Sp Bool)");
    cv_decl("(declare-const Wp Bool)");
    input = "(ite (= S #b1) #b1 W)";                  // select C, true, Y  on i1
    // select poison: poison if the condition is poison, or the SELECTED arm is (true is never poison).
    CV_INPUT_POISON = "(or Sp (and (= S #b0) Wp))";
    out = foldSelectTrueOr(C, W, B, freeze);
  }
  else if (!strncmp(f, "fadd_nnan", 9)) {            // fadd X,Y -> fadd nnan X,Y  (FP fast-math flag)
    bool guarded = !strcmp(f, "fadd_nnan_guarded");
    Value FX{"FX"}, FY{"FY"};                         // i.e. float operands
    cv_decl("(declare-const FX (_ FloatingPoint 8 24))");
    cv_decl("(declare-const FY (_ FloatingPoint 8 24))");
    cv_set_logic("QF_FPBV");                          // reason in the FP theory
    input = "(fp.add RNE FX FY)";                     // source fadd: NaN is a DEFINED value (no poison)
    out = foldFAddNNan(FX, FY, B, guarded);
  }
  else if (!strncmp(f, "load_forward", 12)) {        // store V,P; load Q -> forward V  (must-alias)
    bool guarded = !strcmp(f, "load_forward_guarded");
    Value SV{"VV"}, SP{"PA"}, LP{"QA"};
    cv_decl("(declare-const MEM (Array (_ BitVec 32) (_ BitVec 32)))");
    cv_decl("(declare-const PA (_ BitVec 32))");
    cv_decl("(declare-const QA (_ BitVec 32))");
    cv_decl("(declare-const VV (_ BitVec 32))");
    cv_set_logic("QF_ABV");                          // arrays + bitvectors
    input = "(select (store MEM PA VV) QA)";          // load Q AFTER store P,V
    out = foldStoreToLoadForward(SV, SP, LP, guarded);
  }
  else if (!strncmp(f, "dead_store", 10)) {          // remove store; intervening load reads orig mem (no-alias)
    bool guarded = !strcmp(f, "dead_store_guarded");
    Value SP{"PA"}, LP{"QA"}, MEM{"MEM"};
    cv_decl("(declare-const MEM (Array (_ BitVec 32) (_ BitVec 32)))");
    cv_decl("(declare-const PA (_ BitVec 32))");
    cv_decl("(declare-const QA (_ BitVec 32))");
    cv_decl("(declare-const VV (_ BitVec 32))");
    cv_set_logic("QF_ABV");
    input = "(select (store MEM PA VV) QA)";          // load Q while the store V,P is still present
    out = foldDeadStoreElim(SP, LP, MEM, guarded);
  }
  else if (!strncmp(f, "shr_shl_roundtrip", 17)) {   // mul (lshr X,1),2 -> X  (multi-instr, known-bits)
    bool guarded = !strcmp(f, "shr_shl_roundtrip_guarded");
    Value one{"(_ bv1 32)"}, two{"(_ bv2 32)"}; two.is_const = true;
    Value *shr = cv_node(OP_LSHR, "(bvlshr X (_ bv1 32))", &X, &one);
    Instruction I = *cv_node(OP_MUL, "(bvmul (bvlshr X (_ bv1 32)) (_ bv2 32))", shr, &two);
    input = "(bvmul (bvlshr X (_ bv1 32)) (_ bv2 32))";
    out = foldShrShlRoundtrip(I, B, guarded);
  }
  else if (!strncmp(f, "worklist", 8)) {             // whole multi-instruction pass run, to fixpoint
    bool buggy = !strcmp(f, "worklist_buggy");
    // block:  %1 = add X, 0 ;  %2 = mul %1, 1 ;  %3 = sub %2, %2 ;  return %3   (semantically 0)
    std::vector<WNode> n;
    n.push_back({W_LEAF, -1, false, 0, "X"});         // n0: X
    n.push_back({W_ADD,   0, true,  0, ""});           // n1: add n0, 0
    n.push_back({W_MUL,   1, true,  1, ""});           // n2: mul n1, 1
    n.push_back({W_SUB,   2, false, 2, ""});           // n3: sub n2, n2
    input = wOrig(n, 3);                               // the block's original semantics (the spec)
    Value o; o.t = wRun(n, buggy);                     // the pass's composed output after fixpoint
    out = o;
  }
  else if (!strncmp(f, "speculate_load", 14)) {      // hoist a guarded load -> unconditional (bounds/OOB)
    bool guarded = !strcmp(f, "speculate_load_guarded");
    Value I{"IDX"}, N{"SZ"}, MEM{"MEM"};
    cv_decl("(declare-const MEM (Array (_ BitVec 32) (_ BitVec 32)))");
    cv_decl("(declare-const IDX (_ BitVec 32))");
    cv_decl("(declare-const SZ (_ BitVec 32))");
    cv_set_logic("QF_ABV");
    input = "(ite (bvult IDX SZ) (select MEM IDX) (_ bv0 32))";  // load only inside the bounds guard
    CV_INPUT_POISON = "false";                        // the guarded source is always defined (no OOB)
    out = foldSpeculateLoad(I, N, MEM, guarded);
  }
  else return 1;
  cv_emit(input, out.t.empty() ? nullptr : &out);
  return 0;
}
