// A "symbolic LLVM" shim: the small slice of the LLVM API an InstCombine-style fold calls, but
// every Value is a SYMBOLIC TERM (an SMT-LIB bitvector expression) instead of a concrete value.
// The pass's REAL C++ runs over these; builder calls build output terms, and analysis queries are
// CHOICE POINTS driven by a choice vector (so the harness enumerates the pass's actual control-flow
// paths) that record the decision so the driver can attach each query's semantic precondition.
//
// This lets O2T symbolically execute the genuine pass implementation -- its real branches, not a
// regex -- and discharge `(facts the taken path established) => refines(out, in)` per path.
#ifndef CV_SYMBOLIC_LLVM_H
#define CV_SYMBOLIC_LLVM_H
#include <string>
#include <vector>
#include <cassert>
#include <cstdio>
#include <cstdlib>

// a symbolic SSA value = its SMT term, plus optional instruction structure (opcode + operands) so
// NESTED PatternMatch (m_Add(m_Mul(...), ...)) can recurse, the way real LLVM matchers do.
/* A construct the shim cannot model SOUNDLY. This aborts rather than returning an approximation:
 * the driver sees the harness die and records the path as an ERROR, which `verify_fold` refuses to
 * count as sound. Degrading quietly would let a wrong value (say a truncated mask) flow into an
 * obligation and produce a proof about something other than the fold. Fail closed. */
[[noreturn]] inline void cv_unsupported(const char *why) {
  std::fprintf(stderr, "cv_unsupported: %s\n", why);
  std::abort();
}

enum CvOpcode { OP_OTHER, OP_ICMP, OP_ADD, OP_SUB, OP_MUL, OP_AND, OP_OR, OP_XOR,
                OP_SHL, OP_LSHR, OP_ASHR, OP_UDIV, OP_SDIV, OP_UREM, OP_SREM };

enum CVPredicate { ICMP_EQ, ICMP_NE, ICMP_ULT, ICMP_ULE, ICMP_UGT, ICMP_UGE,
                   ICMP_SLT, ICMP_SLE, ICMP_SGT, ICMP_SGE,
                   FCMP_OEQ, FCMP_ONE, FCMP_UNO, FCMP_ORD };

struct Value {
  std::string t;
  int opcode = 0;                                // 0 == a leaf / non-instruction value
  // Upstream builds instructions with `new ICmpInst(Pred, X, R)` as well as through the IRBuilder,
  // and every instruction class here aliases Value, so that constructor has to live on Value. The
  // string constructors keep the shim's own `Value{"(bvand ...)"}` spelling working, which stopped
  // being aggregate initialisation the moment any constructor was declared.
  Value() = default;
  Value(std::string term) : t(std::move(term)) {}
  Value(const char *term) : t(term) {}
  Value(CVPredicate p, Value *a, Value *b);      // defined once cv_icmp_term is available
  Value *op0 = nullptr, *op1 = nullptr;
  bool is_const = false;                         // a ConstantInt (for isa/m_ConstantInt)
  bool one_use = true;                           // single-use (profitability guards)
  std::string poison = "false";                  // SMT bool: when this value is poison (UB modeling)

  // --- the accessor surface UNMODIFIED upstream folds call on a Value/Instruction. Everything in
  // --- this shim aliases to Value (a symbolic value IS its defining instruction), so the members a
  // --- real fold reaches for -- operands, opcode, type, predicate, select arms, use counts -- live
  // --- here. They are STRUCTURAL: they expose the symbolic term's own structure so the fold's real
  // --- branches can run. No analysis meaning is invented; semantic queries stay choice points.
  typedef CVPredicate Predicate;
  // upstream names these as ICmpInst::ICMP_EQ / CmpInst::ICMP_NE, and every instruction class here
  // aliases to Value, so the enumerators must be reachable as members too.
  static const CVPredicate ICMP_EQ = ::ICMP_EQ, ICMP_NE = ::ICMP_NE,
      ICMP_ULT = ::ICMP_ULT, ICMP_ULE = ::ICMP_ULE, ICMP_UGT = ::ICMP_UGT, ICMP_UGE = ::ICMP_UGE,
      ICMP_SLT = ::ICMP_SLT, ICMP_SLE = ::ICMP_SLE, ICMP_SGT = ::ICMP_SGT, ICMP_SGE = ::ICMP_SGE;
  // upstream spells opcodes `Instruction::Xor` / `BinaryOperator::Add`; both are typedefs of Value
  // here, so the enumerators live on Value and alias the same CvOpcode values `cv_node` records.
  static const int Add = OP_ADD, Sub = OP_SUB, Mul = OP_MUL, And = OP_AND, Or = OP_OR,
                   Xor = OP_XOR, Shl = OP_SHL, LShr = OP_LSHR, AShr = OP_ASHR,
                   UDiv = OP_UDIV, SDiv = OP_SDIV, URem = OP_UREM, SRem = OP_SREM;
  // `Not X` is `xor X, -1`; bvnot is exactly that, so this is the fold's own rewrite, not an
  // approximation of it. The name argument upstream passes is metadata and carries no semantics.
  static Value *CreateNot(Value *v, const std::string & = "");
  static bool isEquality(CVPredicate p) { return p == ::ICMP_EQ || p == ::ICMP_NE; }
  static CVPredicate getInversePredicate(CVPredicate p) { return p == ::ICMP_EQ ? ::ICMP_NE : ::ICMP_EQ; }
  // upstream also asks the INSTANCE -- `LHS->getInversePredicate()` is the inverse of the icmp's OWN
  // predicate. Only meaningful because an icmp node carries one; defined below, where the per-
  // predicate algebra is (the shortcut spelling above collapses everything non-equality to EQ and is
  // wrong for ordered comparisons, which is exactly why the instance form does not reuse it).
  CVPredicate getInversePredicate() const;
  CVPredicate getSwappedPredicate() const;
  Predicate pred = ::ICMP_EQ;
  struct Type *ty = nullptr;
  Value *cond = nullptr, *tval = nullptr, *fval = nullptr;   // select arms, when this is a select
  std::string name;

  Value *getOperand(unsigned i) { return i == 0 ? op0 : op1; }
  Value *getOperand(unsigned i) const { return i == 0 ? op0 : op1; }
  // `exact` on a shift asserts no non-zero bits are shifted out. It is carried per value because a
  // fold may only propagate it when EVERY source shift had it -- dropping that condition would let
  // the rewrite introduce poison the source never had.
  bool exact = false;
  bool isExact() const { return exact; }
  int getOpcode() const { return opcode; }
  bool hasOneUse() const { return one_use; }
  bool hasNUses(unsigned n) const { return one_use && n == 1; }
  Predicate getPredicate() const { return pred; }
  Value *getCondition() { return cond; }
  Value *getTrueValue() { return tval; }
  Value *getFalseValue() { return fval; }
  const std::string &getName() const { return name; }
  struct Type *getType();
  // upstream also builds instructions via the static factories on the instruction
  // classes (`BinaryOperator::CreateSub(A, B)`), not only through the IRBuilder.
  static Value *CreateAShr(Value *a, Value *b);
  static Value *CreateAdd(Value *a, Value *b);
  static Value *CreateAnd(Value *a, Value *b);
  static Value *CreateLShr(Value *a, Value *b);
  static Value *CreateMul(Value *a, Value *b);
  static Value *CreateOr(Value *a, Value *b);
  static Value *CreateSDiv(Value *a, Value *b);
  static Value *CreateShl(Value *a, Value *b);
  static Value *CreateSub(Value *a, Value *b);
  static Value *CreateUDiv(Value *a, Value *b);
  static Value *CreateURem(Value *a, Value *b);
  static Value *CreateXor(Value *a, Value *b);
};

// Stable storage for values the pass holds by POINTER. Unmodified upstream code is written in terms
// of `Value *` throughout -- matchers bind pointers, builders take and return them -- so the shim
// hands back addresses into this arena rather than temporaries.
static Value CV_VARENA_M[256];
static int CV_MPOS_M;
inline Value *cv_keep(const Value &v) { Value *p = &CV_VARENA_M[CV_MPOS_M++]; *p = v; return p; }

static std::vector<int> CV_CHOICES;              // the path being explored (one bit per query)
static size_t CV_IDX = 0;
struct CVDecision { std::string query, arg; int val; };
static std::vector<CVDecision> CV_DECISIONS;

static int cv_next_choice() {
  int c = (CV_IDX < CV_CHOICES.size()) ? CV_CHOICES[CV_IDX] : 0;
  CV_IDX++;
  return c;
}
static Value cv_bv(unsigned long v) {            // an i32 constant term
  char b[64]; snprintf(b, sizeof b, "(_ bv%lu 32)", v); return Value{b};
}

// the SMT predicate "signed `a + b` overflows i32": operands share a sign, the sum's sign differs.
// Shared by CreateNSWAdd (poison), willNotOverflowSignedAdd (the safety query), and source-poison
// construction for nested flagged inputs.
static std::string cv_saddo(const std::string &a, const std::string &b) {
  return "(and (= ((_ extract 31 31) " + a + ") ((_ extract 31 31) " + b + ")) (not (= ((_ extract "
         "31 31) " + a + ") ((_ extract 31 31) (bvadd " + a + " " + b + ")))))";
}

// OR of two poison conditions, kept "false" when both are (so all-defined folds stay unchanged).
inline std::string cv_orp(const std::string &p, const std::string &q) {
  if (p == "false") return q;
  if (q == "false") return p;
  return "(or " + p + " " + q + ")";
}

// --- IRBuilder: each create-call returns the symbolic term of the built instruction ----------
// forward declarations: IRBuilder and ConstantInt are defined before `Type`, and icmp needs both.
struct Type;
struct APInt;
inline Type *cv_i1();
inline std::string cv_ext_term(const Value &v, unsigned to_bits, bool is_signed);
// a value's bit width (Type is defined below, so this is resolved after it)
inline unsigned cv_width(const Value &v);
inline void cv_decl(const std::string &smt);     // extra SMT declarations (defined below)
static int CV_FRZ;                               // names the arbitrary value each freeze may choose
inline std::string cv_icmp_term(CVPredicate p, const std::string &a, const std::string &b);

// upstream signatures name the base class; the shim has a single builder type.
struct IRBuilder {
  // upstream passes and receives `Value *`; these forward to the by-value forms below.
  Value *CreateAShr(Value *a, Value *b) { return cv_keep(CreateAShr(*a, *b)); }
  // upstream: CreateAShr(LHS, RHS, Name, isExact). `exact` asserts nothing non-zero was shifted
  // out, i.e. (result << Y) == X; when the flag is set without that holding, the value is poison.
  Value *CreateAShr(Value *a, Value *b, const std::string &, bool isExact) {
    Value *r = cv_keep(Value{"(bvashr " + a->t + " " + b->t + ")"});
    r->opcode = OP_ASHR; r->op0 = a; r->op1 = b; r->exact = isExact;
    r->poison = cv_orp(a->poison, b->poison);
    if (isExact) {
      std::string back = "(bvshl (bvashr " + a->t + " " + b->t + ") " + b->t + ")";
      r->poison = cv_orp(r->poison, "(not (= " + back + " " + a->t + "))");
    }
    return r;
  }
  Value *CreateAShr(Value *a, Value b)  { return cv_keep(CreateAShr(*a, b)); }
  Value *CreateAShr(Value a, Value *b)  { return cv_keep(CreateAShr(a, *b)); }
  Value *CreateAdd(Value *a, Value *b) { return cv_keep(CreateAdd(*a, *b)); }
  Value *CreateAdd(Value *a, Value b)  { return cv_keep(CreateAdd(*a, b)); }
  Value *CreateAdd(Value a, Value *b)  { return cv_keep(CreateAdd(a, *b)); }
  Value *CreateAnd(Value *a, Value *b) { return cv_keep(CreateAnd(*a, *b)); }
  Value *CreateAnd(Value *a, Value b)  { return cv_keep(CreateAnd(*a, b)); }
  Value *CreateAnd(Value a, Value *b)  { return cv_keep(CreateAnd(a, *b)); }
  Value *CreateLShr(Value *a, Value *b) { return cv_keep(CreateLShr(*a, *b)); }
  Value *CreateLShr(Value *a, Value b)  { return cv_keep(CreateLShr(*a, b)); }
  Value *CreateLShr(Value a, Value *b)  { return cv_keep(CreateLShr(a, *b)); }
  Value *CreateMul(Value *a, Value *b) { return cv_keep(CreateMul(*a, *b)); }
  Value *CreateMul(Value *a, Value b)  { return cv_keep(CreateMul(*a, b)); }
  Value *CreateMul(Value a, Value *b)  { return cv_keep(CreateMul(a, *b)); }
  Value *CreateOr(Value *a, Value *b) { return cv_keep(CreateOr(*a, *b)); }
  Value *CreateOr(Value *a, Value b)  { return cv_keep(CreateOr(*a, b)); }
  Value *CreateOr(Value a, Value *b)  { return cv_keep(CreateOr(a, *b)); }
  Value *CreateOrPoisoning(Value *a, Value *b) { return cv_keep(CreateOrPoisoning(*a, *b)); }
  Value *CreateOrPoisoning(Value *a, Value b)  { return cv_keep(CreateOrPoisoning(*a, b)); }
  Value *CreateOrPoisoning(Value a, Value *b)  { return cv_keep(CreateOrPoisoning(a, *b)); }
  Value *CreateSDiv(Value *a, Value *b) { return cv_keep(CreateSDiv(*a, *b)); }
  Value *CreateSDiv(Value *a, Value b)  { return cv_keep(CreateSDiv(*a, b)); }
  Value *CreateSDiv(Value a, Value *b)  { return cv_keep(CreateSDiv(a, *b)); }
  Value *CreateShl(Value *a, Value *b) { return cv_keep(CreateShl(*a, *b)); }
  Value *CreateShl(Value *a, Value b)  { return cv_keep(CreateShl(*a, b)); }
  Value *CreateShl(Value a, Value *b)  { return cv_keep(CreateShl(a, *b)); }
  Value *CreateSub(Value *a, Value *b) { return cv_keep(CreateSub(*a, *b)); }
  Value *CreateSub(Value *a, Value b)  { return cv_keep(CreateSub(*a, b)); }
  Value *CreateSub(Value a, Value *b)  { return cv_keep(CreateSub(a, *b)); }
  Value *CreateUDiv(Value *a, Value *b) { return cv_keep(CreateUDiv(*a, *b)); }
  Value *CreateUDiv(Value *a, Value b)  { return cv_keep(CreateUDiv(*a, b)); }
  Value *CreateUDiv(Value a, Value *b)  { return cv_keep(CreateUDiv(a, *b)); }
  Value *CreateURem(Value *a, Value *b) { return cv_keep(CreateURem(*a, *b)); }
  Value *CreateURem(Value *a, Value b)  { return cv_keep(CreateURem(*a, b)); }
  Value *CreateURem(Value a, Value *b)  { return cv_keep(CreateURem(a, *b)); }
  Value *CreateXor(Value *a, Value *b) { return cv_keep(CreateXor(*a, *b)); }
  Value *CreateXor(Value *a, Value b)  { return cv_keep(CreateXor(*a, b)); }
  Value *CreateXor(Value a, Value *b)  { return cv_keep(CreateXor(a, *b)); }

  // icmp yields a ONE-BIT term, so it composes with CreateSelect's `(= c (_ bv1 1))` test rather
  // than being conflated with the i32 default -- comparing a 1-bit term to a 32-bit one is exactly
  // the kind of silent mismatch this shim has to avoid.
  Value *CreateICmp(CVPredicate p, Value *a, Value *b, const std::string & = "") {
    Value *r = cv_keep(Value{cv_icmp_term(p, a->t, b->t)});
    r->opcode = OP_ICMP; r->pred = p; r->op0 = a; r->op1 = b; r->ty = cv_i1();
    return r;
  }
  Value *CreateIsNotNull(Value *a, const std::string & = "") {
    return CreateICmp(::ICMP_NE, a, cv_keep(Value{"(_ bv0 32)"}));
  }
  Value *CreateNot(Value *v, const std::string & = "") { return cv_keep(Value{"(bvnot " + v->t + ")"}); }
  Value CreateNot(Value v) { return {"(bvnot " + v.t + ")"}; }
  Value CreateAnd(Value a, Value b) { return {"(bvand " + a.t + " " + b.t + ")"}; }
  Value CreateOr(Value a, Value b)  { return {"(bvor " + a.t + " " + b.t + ")"}; }
  Value CreateXor(Value a, Value b) { return {"(bvxor " + a.t + " " + b.t + ")"}; }
  Value CreateAdd(Value a, Value b) { return {"(bvadd " + a.t + " " + b.t + ")"}; }
  Value CreateSub(Value a, Value b) { return {"(bvsub " + a.t + " " + b.t + ")"}; }
  Value CreateMul(Value a, Value b) { return {"(bvmul " + a.t + " " + b.t + ")"}; }
  Value CreateShl(Value a, Value b) { return {"(bvshl " + a.t + " " + b.t + ")"}; }
  Value CreateLShr(Value a, Value b){ return {"(bvlshr " + a.t + " " + b.t + ")"}; }
  Value CreateAShr(Value a, Value b){ return {"(bvashr " + a.t + " " + b.t + ")"}; }
  Value CreateUDiv(Value a, Value b){ return {"(bvudiv " + a.t + " " + b.t + ")"}; }
  Value CreateURem(Value a, Value b){ return {"(bvurem " + a.t + " " + b.t + ")"}; }
  Value CreateSDiv(Value a, Value b){ return {"(bvsdiv " + a.t + " " + b.t + ")"}; }
  Value CreateSelect(Value c, Value x, Value y) {
    return {"(ite (= " + c.t + " (_ bv1 1)) " + x.t + " " + y.t + ")"};
  }
  // Upstream's `CreateSelect(Cond, T, F, Name, MDFrom)` -- the trailing name and metadata source
  // carry no semantics, but the ARITY has to match or the fold does not compile. Unlike the other
  // unflagged builders this one propagates POISON, because a select's rule is specific and cheap to
  // state: poison if the condition is, or if the SELECTED arm is. Modelling more poison on a target
  // can only make a proof harder, never a refutation weaker.
  Value *CreateSelect(Value *c, Value *x, Value *y, const std::string & = "", Value * = nullptr) {
    Value *r = cv_keep(Value{"(ite (= " + c->t + " (_ bv1 1)) " + x->t + " " + y->t + ")"});
    r->cond = c; r->tval = x; r->fval = y; r->ty = x->ty;
    r->poison = cv_orp(c->poison, "(ite (= " + c->t + " (_ bv1 1)) " + x->poison + " " + y->poison + ")");
    return r;
  }
  // POISON-producing flagged ops. `add nsw X, Y` is poison on SIGNED overflow (operands share a
  // sign but the sum's sign differs) -- a fold that sets nsw without proving no-overflow is unsound.
  Value CreateNSWAdd(Value x, Value y) {
    Value r; r.t = "(bvadd " + x.t + " " + y.t + ")"; r.poison = cv_saddo(x.t, y.t); return r;
  }
  // `add nuw X, Y` is poison on UNSIGNED overflow: the sum wraps below an operand, (x+y) <u x.
  Value CreateNUWAdd(Value x, Value y) {
    Value r; r.t = "(bvadd " + x.t + " " + y.t + ")";
    r.poison = "(bvult (bvadd " + x.t + " " + y.t + ") " + x.t + ")"; return r;
  }
  // `or disjoint X, Y` -- the disjoint flag asserts the operands share no set bits; it is poison
  // when (X & Y) != 0. (The VALUE `or X,Y` also only equals `add X,Y` when X&Y==0, so this fold
  // needs the same fact for BOTH its value-correctness and its flag -- refinement discharges both.)
  Value CreateOrDisjoint(Value x, Value y) {
    Value r; r.t = "(bvor " + x.t + " " + y.t + ")";
    r.poison = "(not (= (bvand " + x.t + " " + y.t + ") (_ bv0 32)))"; return r;
  }
  // `udiv exact X, Y` asserts Y divides X with NO remainder; poison when (X urem Y) != 0. Unlike the
  // overflow flags, the poison here depends on the operand VALUES, not just their signs.
  Value CreateExactUDiv(Value x, Value y) {
    Value r; r.t = "(bvudiv " + x.t + " " + y.t + ")";
    r.poison = "(not (= (bvurem " + x.t + " " + y.t + ") (_ bv0 32)))"; return r;
  }
  // poison-CONTAGION `or`: the result is poison if EITHER operand is (unlike the flag ops, the
  // poison comes from the inputs, not a flag). Used to expose the select->or poison unsoundness.
  Value CreateOrPoisoning(Value a, Value b) {
    Value r; r.t = "(bvor " + a.t + " " + b.t + ")"; r.poison = cv_orp(a.poison, b.poison); return r;
  }
  // `freeze` stops poison propagation: the result is ALWAYS defined. But WHAT it yields where the
  // operand is poison is an ARBITRARY value the implementation chooses -- not the poison operand's
  // term. This used to be modelled as `r.t = a.t` with the poison cleared, which says freeze(poison)
  // equals whatever term `a` happened to carry. That is strictly stronger than the semantics allow,
  // and stronger in the dangerous direction: a rewrite whose correctness depends on the frozen value
  // being a's value would be PROVED here and be wrong in LLVM. It survived because the one fold that
  // built a freeze (the select->or poison fold) is correct for ANY frozen value, so its obligation
  // never consulted the choice -- the shim's standing hazard, code that compiles, runs, and has
  // never been asked the question it gets wrong.
  //
  // The sound model NAMES the choice: a fresh unconstrained value, selected exactly where the
  // operand is poison. Where the operand is provably defined the two models coincide, and the fresh
  // constant is skipped rather than declared, so poison-free folds keep their old obligation
  // verbatim. Track B reached the same encoding for the same reason (scalar_ir's `fresh` list).
  Value CreateFreeze(Value a) {
    Value r;
    r.poison = "false";
    if (a.poison == "false") { r.t = a.t; return r; }        // nothing to choose
    std::string f = "FRZ" + std::to_string(CV_FRZ++);
    cv_decl("(declare-const " + f + " (_ BitVec " + std::to_string(cv_width(a)) + "))");
    r.t = "(ite " + a.poison + " " + f + " " + a.t + ")";
    return r;
  }
  Value *CreateFreeze(Value *a) { return cv_keep(CreateFreeze(*a)); }
  // fast-math `fadd nnan X, Y`: the nnan flag asserts the result is never NaN; it is poison when the
  // sum actually IS NaN (e.g. +inf + -inf). The FP analogue of nsw -- a flag the pass must justify.
  Value CreateFAddNNan(Value x, Value y) {
    Value r; r.t = "(fp.add RNE " + x.t + " " + y.t + ")";
    r.poison = "(fp.isNaN (fp.add RNE " + x.t + " " + y.t + "))"; return r;
  }
};
/* ConstantInt::get(...) -- the real LLVM constant factory; the result `isa<ConstantInt>`. */
struct SExtInst : Value { SExtInst(Value *v, Type *ty, const std::string & = ""); };
struct ZExtInst : Value { ZExtInst(Value *v, Type *ty, const std::string & = ""); };
struct ConstantInt {
  static Value get(unsigned long v) { Value r = cv_bv(v); r.is_const = true; return r; }
  static Value *get(Type *ty, const APInt &a);
  static Value *get(Type *ty, unsigned long n);
  static Value *getNullValue(Type *ty);
  static Value *getAllOnesValue(Type *ty);
};
typedef Value BinaryOperator;
typedef IRBuilder IRBuilderBase;                     /* a fold's `BinaryOperator &I` is a Value */

// --- enough of the PASS CLASS and instruction hierarchy that UNMODIFIED upstream fold definitions
// --- parse against this shim. Measured over LLVM 18's InstCombine sources: 89 of 106 fold-shaped
// --- functions sit within 8 missing symbols of compiling here, and the single most-wanted is the
// --- enclosing class itself -- an `InstCombinerImpl::foldX(...)` definition cannot even parse
// --- without it. These are STRUCTURAL declarations: they let real source compile so the symbolic
// --- executor can run its ACTUAL branches. They deliberately model no analysis semantics; every
// --- query that carries meaning goes through the choice-point machinery above.
struct Type {
  unsigned bits = 32;
  unsigned getScalarSizeInBits() const { return bits; }
  unsigned getIntegerBitWidth() const { return bits; }
  bool isIntOrIntVectorTy() const { return true; }
  bool isVectorTy() const { return false; }
  Type *getScalarType() { return this; }
};
static Type CV_I32;
static Type CV_I1{1};
inline Type *cv_i1() { return &CV_I1; }
inline unsigned cv_width(const Value &v) { return v.ty ? v.ty->bits : 32; }
inline SExtInst::SExtInst(Value *v, Type *ty, const std::string &) {
  t = cv_ext_term(*v, ty ? ty->bits : 32, /*is_signed=*/true);
  this->ty = ty; poison = v->poison; op0 = v;
}
inline ZExtInst::ZExtInst(Value *v, Type *ty, const std::string &) {
  t = cv_ext_term(*v, ty ? ty->bits : 32, /*is_signed=*/false);
  this->ty = ty; poison = v->poison; op0 = v;
}
// Width-changing casts. These are NOT views: `sext i1 %c to i32` is a different value from %c, so
// the term widens and the recorded type changes with it. A cast to a NARROWER type would be a
// truncation and is refused rather than silently ignored.
inline std::string cv_ext_term(const Value &v, unsigned to_bits, bool is_signed) {
  unsigned from = v.ty ? v.ty->bits : 32;
  if (to_bits == from) return v.t;
  if (to_bits < from) cv_unsupported("sext/zext to a NARROWER type is a truncation");
  return "((_ " + std::string(is_signed ? "sign_extend " : "zero_extend ") +
         std::to_string(to_bits - from) + ") " + v.t + ")";
}

// Width-changing casts: declared here, defined once `Type` is complete.
inline Value *cv_allones() { return cv_keep(Value{"(_ bv4294967295 32)"}); }
inline Type *Value::getType() { return ty ? ty : &CV_I32; }
inline Value *Value::CreateAShr(Value *a, Value *b) { return cv_keep(Value{"(bvashr " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateAdd(Value *a, Value *b) { return cv_keep(Value{"(bvadd " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateAnd(Value *a, Value *b) { return cv_keep(Value{"(bvand " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateLShr(Value *a, Value *b) { return cv_keep(Value{"(bvlshr " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateMul(Value *a, Value *b) { return cv_keep(Value{"(bvmul " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateNot(Value *v, const std::string &) { return cv_keep(Value{"(bvnot " + v->t + ")"}); }
inline Value *Value::CreateOr(Value *a, Value *b) { return cv_keep(Value{"(bvor " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateSDiv(Value *a, Value *b) { return cv_keep(Value{"(bvsdiv " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateShl(Value *a, Value *b) { return cv_keep(Value{"(bvshl " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateSub(Value *a, Value *b) { return cv_keep(Value{"(bvsub " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateUDiv(Value *a, Value *b) { return cv_keep(Value{"(bvudiv " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateURem(Value *a, Value *b) { return cv_keep(Value{"(bvurem " + a->t + " " + b->t + ")"}); }
inline Value *Value::CreateXor(Value *a, Value *b) { return cv_keep(Value{"(bvxor " + a->t + " " + b->t + ")"}); }

// `Instruction` is already an alias for Value further down (a symbolic value IS its defining
// instruction here), so the opcode enum lives beside it rather than in a competing class.
namespace CVOpcodes {
  enum BinaryOps { Add = 1, Sub, Mul, And, Or, Xor, Shl, LShr, AShr, UDiv, SDiv, URem, SRem };
}
// Predicate algebra, spelled out per predicate. The previous shim returned the argument unchanged
// from getSwappedPredicate and collapsed everything non-equality to ICMP_EQ in
// getInversePredicate -- both compile, read plausibly, and denote the wrong comparison. They were
// only harmless while no fold called them; modelling icmp makes them reachable.
inline CVPredicate cv_swap_pred(CVPredicate p) {
  switch (p) {
    case ICMP_EQ: case ICMP_NE: return p;              // symmetric
    case ICMP_ULT: return ICMP_UGT;  case ICMP_UGT: return ICMP_ULT;
    case ICMP_ULE: return ICMP_UGE;  case ICMP_UGE: return ICMP_ULE;
    case ICMP_SLT: return ICMP_SGT;  case ICMP_SGT: return ICMP_SLT;
    case ICMP_SLE: return ICMP_SGE;  case ICMP_SGE: return ICMP_SLE;
  }
  cv_unsupported("getSwappedPredicate on an unmodelled predicate");
}
inline CVPredicate cv_inverse_pred(CVPredicate p) {
  switch (p) {
    case ICMP_EQ:  return ICMP_NE;   case ICMP_NE:  return ICMP_EQ;
    case ICMP_ULT: return ICMP_UGE;  case ICMP_UGE: return ICMP_ULT;
    case ICMP_ULE: return ICMP_UGT;  case ICMP_UGT: return ICMP_ULE;
    case ICMP_SLT: return ICMP_SGE;  case ICMP_SGE: return ICMP_SLT;
    case ICMP_SLE: return ICMP_SGT;  case ICMP_SGT: return ICMP_SLE;
  }
  cv_unsupported("getInversePredicate on an unmodelled predicate");
}
inline CVPredicate Value::getInversePredicate() const { return cv_inverse_pred(pred); }
inline CVPredicate Value::getSwappedPredicate() const { return cv_swap_pred(pred); }

struct CmpInst {
  typedef CVPredicate Predicate;
  static bool isEquality(Predicate p) { return p == ICMP_EQ || p == ICMP_NE; }
  static Predicate getInversePredicate(Predicate p) { return cv_inverse_pred(p); }
  static Predicate getSwappedPredicate(Predicate p) { return cv_swap_pred(p); }
  static const CVPredicate ICMP_EQ = ::ICMP_EQ, ICMP_NE = ::ICMP_NE,
                           ICMP_ULT = ::ICMP_ULT, ICMP_ULE = ::ICMP_ULE,
                           ICMP_UGT = ::ICMP_UGT, ICMP_UGE = ::ICMP_UGE,
                           ICMP_SLT = ::ICMP_SLT, ICMP_SLE = ::ICMP_SLE,
                           ICMP_SGT = ::ICMP_SGT, ICMP_SGE = ::ICMP_SGE;
};
typedef Value ICmpInst;
// `cast<T>(V)` is the CALLER asserting the class -- upstream only writes it where the class is
// already established, so an identity view is faithful. `dyn_cast<T>` is deliberately NOT extended
// to these classes: every instruction class here is a typedef of `Value`, so nothing can tell them
// apart, and a dyn_cast that always succeeds would send the executor down branches the real pass
// would never take. Folds needing it stay blocked rather than being modelled wrongly.
template <class T> T *cast(Value *v) { return v; }
template <class T> const T *cast(const Value *v) { return v; }
template <class T> T &cast(Value &v) { return v; }
typedef Value FreezeInst;
typedef Value IntrinsicInst;
typedef Value FCmpInst;
typedef Value SelectInst;
typedef Value CallInst;
struct Function {};
typedef Value Constant;
struct APInt {
  unsigned long v = 0, bits = 32;
  APInt() = default;
  APInt(unsigned w, unsigned long val) : v(val & _mask(w)), bits(w) {}
  bool isZero() const { return v == 0; }
  bool isOne() const { return v == 1; }
  // all-ones is all-ones AT THIS WIDTH. Hard-coding 0xFFFFFFFF made an i8 mask of 0xFF answer NO,
  // which is not a near-miss: `isAllOnes` is how a fold recognises `~0`, so the arm simply never
  // fires. The rest of this class already carries the width for exactly this reason.
  bool isAllOnes() const { return v == _mask(bits); }
  unsigned getBitWidth() const { return bits; }
  unsigned long getZExtValue() const { return v; }
  // Mask arithmetic real folds do on CONSTANT operands. Host-side values, so this is exact integer
  // arithmetic rather than an approximation -- but the WIDTH is carried, because `~C` and
  // isPowerOf2 both depend on it and a 64-bit complement of a 32-bit mask is a different number.
  static unsigned long _mask(unsigned n) { return n >= 64 ? ~0ul : ((1ul << n) - 1ul); }
  APInt operator&(const APInt &o) const { APInt r = *this; r.v &= o.v; return r; }
  APInt operator|(const APInt &o) const { APInt r = *this; r.v |= o.v; return r; }
  APInt operator^(const APInt &o) const { APInt r = *this; r.v ^= o.v; return r; }
  APInt &operator&=(const APInt &o) { v &= o.v; return *this; }
  APInt &operator^=(const APInt &o) { v ^= o.v; return *this; }
  APInt operator~() const { APInt r = *this; r.v = (~v) & _mask(bits); return r; }
  bool operator==(const APInt &o) const { return v == o.v; }
  bool operator!=(const APInt &o) const { return v != o.v; }
  bool operator==(unsigned long n) const { return v == n; }
  bool operator!=(unsigned long n) const { return v != n; }
  bool isPowerOf2() const { return v != 0 && (v & (v - 1)) == 0; }
  bool ult(const APInt &o) const { return v < o.v; }
  bool isNegative() const { return bits && bits < 64 && ((v >> (bits - 1)) & 1); }
  // getMaxValue(n) is the all-ones value OF WIDTH n (2^n - 1), which is how folds build half-width
  // masks; getMinValue is 0. Widths >= 64 would overflow the host word, so they are refused rather
  // than silently truncated -- a wrong mask would silently weaken every obligation built from it.
  static APInt getMaxValue(unsigned n) {
    if (n >= 64) cv_unsupported("APInt::getMaxValue width >= 64");
    APInt a; a.bits = n; a.v = (n == 0) ? 0ul : ((1ul << n) - 1ul); return a;
  }
  static APInt getMinValue(unsigned n) { APInt a; a.bits = n; a.v = 0; return a; }
  static APInt getAllOnes(unsigned n) { return getMaxValue(n); }
  // SIGNED extremes: at width n, the largest signed value is 2^(n-1) - 1 and the smallest is the
  // sign bit alone. Folds that rewrite signed comparisons compute their new bound from these, so
  // getting the width wrong here silently changes the CONSTANT the rewrite compares against -- which
  // still type-checks and still looks like a fold.
  static APInt getSignedMaxValue(unsigned n) {
    if (n == 0 || n >= 64) cv_unsupported("APInt::getSignedMaxValue width 0 or >= 64");
    APInt a; a.bits = n; a.v = (1ul << (n - 1)) - 1ul; return a;
  }
  static APInt getSignedMinValue(unsigned n) {
    if (n == 0 || n >= 64) cv_unsupported("APInt::getSignedMinValue width 0 or >= 64");
    APInt a; a.bits = n; a.v = 1ul << (n - 1); return a;
  }
  bool isMinSignedValue() const { return bits && v == (1ul << (bits - 1)); }
  bool isMinValue() const { return v == 0; }
  // TWO'S-COMPLEMENT ARITHMETIC, masked back to this value's width. `-C` and `MAX - C` are how the
  // signed/unsigned comparison folds build their new bound, and an unmasked host subtraction would
  // borrow into bits the value does not have -- 0 - 1 is 0xFF at i8, not 0xFFFFFFFFFFFFFFFF.
  APInt operator+(const APInt &o) const { APInt r = *this; r.v = (v + o.v) & _mask(bits); return r; }
  APInt operator-(const APInt &o) const { APInt r = *this; r.v = (v - o.v) & _mask(bits); return r; }
  APInt operator+(unsigned long n) const { APInt r = *this; r.v = (v + n) & _mask(bits); return r; }
  APInt operator-(unsigned long n) const { APInt r = *this; r.v = (v - n) & _mask(bits); return r; }
  APInt operator-() const { APInt r = *this; r.v = (0ul - v) & _mask(bits); return r; }
  bool operator!() const { return v == 0; }
};

// `m_APInt(C)` binds the matched constant's VALUE, and until a fold actually used it the binding
// was never written -- the matcher recorded where to store and then stored nothing, leaving the
// caller's `const APInt *` uninitialised. The first fold to dereference one segfaulted on all 16
// paths. Constants live in an arena so the bound pointer stays valid for the fold's lifetime.
static APInt CV_APARENA[64];
static int CV_APPOS;
inline const APInt *cv_apint_of(const Value &v) {
  // the shim's only constant spelling is "(_ bvN W)"; anything else FAILS the match rather than
  // being guessed at, since a wrong constant would silently change the obligation.
  if (v.t.compare(0, 5, "(_ bv") != 0) return nullptr;
  size_t sp = v.t.find(' ', 5);
  if (sp == std::string::npos) return nullptr;
  APInt *a = &CV_APARENA[(CV_APPOS++) % 64];
  a->v = std::strtoul(v.t.c_str() + 5, nullptr, 10);
  a->bits = std::strtoul(v.t.c_str() + sp + 1, nullptr, 10);
  return a;
}

inline Value *ConstantInt::get(Type *ty, const APInt &a) {
  unsigned w = ty ? ty->bits : 32;
  Value *v = cv_keep(Value{"(_ bv" + std::to_string(a.v & APInt::_mask(w)) + " " + std::to_string(w) + ")"});
  v->is_const = true; v->ty = ty; return v;
}
inline Value *ConstantInt::get(Type *ty, unsigned long n) {
  APInt a; a.v = n; a.bits = ty ? ty->bits : 32; return get(ty, a);
}
inline Value *ConstantInt::getNullValue(Type *ty)    { return get(ty, 0ul); }
inline Value *ConstantInt::getAllOnesValue(Type *ty) { return get(ty, APInt::_mask(ty ? ty->bits : 32)); }
inline std::string cv_icmp_term(CVPredicate p, const std::string &a, const std::string &b) {
  const char *op = nullptr;
  switch (p) {
    case ::ICMP_EQ:  return "(ite (= " + a + " " + b + ") (_ bv1 1) (_ bv0 1))";
    case ::ICMP_NE:  return "(ite (= " + a + " " + b + ") (_ bv0 1) (_ bv1 1))";
    case ::ICMP_ULT: op = "bvult"; break;   case ::ICMP_ULE: op = "bvule"; break;
    case ::ICMP_UGT: op = "bvugt"; break;   case ::ICMP_UGE: op = "bvuge"; break;
    case ::ICMP_SLT: op = "bvslt"; break;   case ::ICMP_SLE: op = "bvsle"; break;
    case ::ICMP_SGT: op = "bvsgt"; break;   case ::ICMP_SGE: op = "bvsge"; break;
    default: cv_unsupported("icmp predicate not modelled");   // never guess a comparison
  }
  return "(ite (" + std::string(op) + " " + a + " " + b + ") (_ bv1 1) (_ bv0 1))";
}


// `new ICmpInst(Pred, A, B)` -- the icmp node, identical to what IRBuilder::CreateICmp builds. It
// carries the predicate and operands so a later fold can match on them, and its type is i1: an icmp
// is a one-bit value, and conflating it with the i32 default is the mismatch this shim exists to
// avoid.
inline Value::Value(CVPredicate p, Value *a, Value *b) {
  t = cv_icmp_term(p, a->t, b->t);
  opcode = OP_ICMP; pred = p; op0 = a; op1 = b; ty = cv_i1();
}

// The pass class an upstream fold is a member of. `Builder` is the shim's symbolic IRBuilder, so a
// verbatim `Builder.CreateXor(...)` in real source builds a symbolic term exactly as the fold does.
struct InstCombiner {
  using BuilderTy = IRBuilder;
  IRBuilder Builder;
  // the InstCombine rewrite sink: the fold's returned replacement value
  Value *replaceInstUsesWith(Value &I, Value *V) { return V; }
  Value *replaceOperand(Value &I, unsigned, Value *V) { return V; }
};
struct InstCombinerImpl : InstCombiner {};
template <class T> bool isa(const Value &v);
template <> inline bool isa<ConstantInt>(const Value &v)   { return v.is_const; }
template <> inline bool isa<BinaryOperator>(const Value &v){ return v.opcode != 0; }
template <class T> const Value *dyn_cast(const Value &v)   { return isa<T>(v) ? &v : nullptr; }

/* defining constraints emitted for derived values (e.g. logBase2 of a captured constant) or facts
 * an analysis query establishes (e.g. no-signed-overflow), added to the path condition by the
 * driver so APInt-derived / poison-aware rewrites can be discharged symbolically. */
static std::vector<std::string> CV_CONS;
inline void cv_constraint(const std::string &smt) { CV_CONS.push_back(smt); }

/* extra SMT declarations a fold needs beyond the driver's default i32 vars -- e.g. i1 operands or
 * Bool operand-poison flags for poison-CONTAGION folds. Emitted in the path; the driver prepends them. */
static std::vector<std::string> CV_DECLS;
inline void cv_decl(const std::string &smt) { CV_DECLS.push_back(smt); }

/* the SMT logic the path must be discharged under (default integer bitvectors). A fold reasoning
 * about floating-point / fast-math flags raises it to QF_FPBV. */
static std::string CV_LOGIC = "QF_BV";
inline void cv_set_logic(const std::string &l) { CV_LOGIC = l; }

/* an analysis query proving a flagged op is safe: willNotOverflowSignedAdd(X,Y) -- when it holds,
 * the pass may set nsw. Establishes the no-signed-overflow fact on this path. */
inline bool willNotOverflowSignedAdd(Value x, Value y) {
  int c = cv_next_choice();
  if (c) cv_constraint("(not " + cv_saddo(x.t, y.t) + ")");
  return c;
}

/* willNotOverflowUnsignedAdd(X,Y) -- when true, the pass may set nuw; establishes (x+y) >=u x. */
inline bool willNotOverflowUnsignedAdd(Value x, Value y) {
  int c = cv_next_choice();
  if (c) cv_constraint("(bvuge (bvadd " + x.t + " " + y.t + ") " + x.t + ")");
  return c;
}

/* haveNoCommonBitsSet(X,Y) -- when true, X&Y==0; lets a pass rewrite add->or and set `disjoint`. */
inline bool haveNoCommonBitsSet(Value x, Value y) {
  int c = cv_next_choice();
  if (c) cv_constraint("(= (bvand " + x.t + " " + y.t + ") (_ bv0 32))");
  return c;
}

/* isKnownExactUDiv(X,Y) -- when true, Y divides X exactly (X urem Y == 0); lets a pass set `exact`. */
inline bool isKnownExactUDiv(Value x, Value y) {
  int c = cv_next_choice();
  if (c) cv_constraint("(= (bvurem " + x.t + " " + y.t + ") (_ bv0 32))");
  return c;
}

/* willNotBeNaN(X,Y) -- when true, X+Y is never NaN; lets a pass set the fast-math `nnan` flag. */
inline bool willNotBeNaN(Value x, Value y) {
  int c = cv_next_choice();
  if (c) cv_constraint("(not (fp.isNaN (fp.add RNE " + x.t + " " + y.t + ")))");
  return c;
}

/* isMustAlias(P,Q) -- when true, the two pointers are provably the SAME address (P == Q). A store-
 * to-load forward is sound only under must-alias; a forward justified by anything weaker is unsound. */
inline bool isMustAlias(Value p, Value q) {
  int c = cv_next_choice();
  if (c) cv_constraint("(= " + p.t + " " + q.t + ")");
  return c;
}

/* isNoAlias(P,Q) -- when true, the pointers are provably DISTINCT (P != Q). Removing a store to P is
 * sound past an intervening load of Q only under no-alias; weaker justification changes the load. */
inline bool isNoAlias(Value p, Value q) {
  int c = cv_next_choice();
  if (c) cv_constraint("(not (= " + p.t + " " + q.t + "))");
  return c;
}

/* a KNOWN-BITS query: isLowBitZero(X) -- when true, X is even (bit 0 == 0). Lets a pass treat
 * (X >> 1) << 1 as X, which otherwise drops X's low bit. */
inline bool isLowBitZero(Value x) {
  int c = cv_next_choice();
  if (c) cv_constraint("(= ((_ extract 0 0) " + x.t + ") #b0)");
  return c;
}

/* a PROVENANCE/bounds query: isInBounds(I,N) -- when true, index I is within [0,N) so a load/store of
 * element I is defined. Speculating a memory access out of its guard is UB unless this holds. */
inline bool isInBounds(Value i, Value n) {
  int c = cv_next_choice();
  if (c) cv_constraint("(bvult " + i.t + " " + n.t + ")");
  return c;
}

/* APInt-style methods on a captured constant `C`. logBase2 returns its FLOOR-log2 (the faithful
 * APInt semantics, defined for any C>0) as a fresh K constrained by 2^K <= C < 2^(K+1). This makes
 * the guard load-bearing: mul X,C == shl X,K only when C == 2^K, i.e. C a power of two -- so a fold
 * that omits the power-of-two check is refuted, not vacuously proved. */
inline Value cv_logBase2(Value /*C*/) {
  cv_constraint("(bvult K (_ bv32 32))");
  cv_constraint("(bvule (bvshl (_ bv1 32) K) C)");                                  /* 2^K <= C */
  cv_constraint("(or (= K (_ bv31 32)) (bvugt (bvshl (_ bv1 32) (bvadd K (_ bv1 32))) C))");  /* C < 2^(K+1) */
  return Value{"K"};
}

// --- a PatternMatch subset (recursive, so REAL nested 3rd-party idioms compile) ---------------
// `match(I, m_Sub(m_Mul(m_Value(A), m_Value(B)), m_Value(C)))` returns whether I has that tree
// shape and captures the leaves -- the same composable matchers LLVM passes use. Matchers live in
// a static pool so nested `m_*(...)` calls stay valid through the match.

enum CvMKind { MK_VALUE, MK_SPECIFIC, MK_CONSTANT, MK_ZERO, MK_ONE, MK_ALLONES, MK_BINOP,
               MK_COMBINEAND, MK_DEFERRED, MK_ANYBINOP, MK_ICMP, MK_CONSTCMP,
               MK_SPECIFICINT, MK_ONEUSE, MK_COMBINEOR };
struct Matcher {
  int kind, opcode;
  Value *cap = nullptr;                          // MK_VALUE / MK_CONSTANT: where to store
  CVPredicate *pred_out = nullptr;               // MK_ICMP: where to store the predicate
  Value **deferred = nullptr;                    // MK_DEFERRED: the BINDING to re-read at match time
  // UNMODIFIED upstream folds declare `Value *A, *B;` and write `m_Value(A)`, i.e. they bind a
  // POINTER, while folds authored against this shim bind a reference. Supporting both is what lets
  // real InstCombine source compile here at all -- it was the single most pervasive difference.
  Value **capp = nullptr;
  const Value *specific;                         // MK_SPECIFIC: the value to compare against
  unsigned long imm;                             // MK_SPECIFICINT: the constant to match
  unsigned imm_bits = 32;                        // MK_CONSTCMP: width of the threshold
  CVPredicate pred = ICMP_EQ;                    // MK_CONSTCMP: the predicate to satisfy
  const APInt **apint = nullptr;                 // MK_CONSTANT via m_APInt: where to store
  bool commutative = false;                       // MK_BINOP: try both operand orders (m_c_*)
  Matcher *a, *b;                                // MK_BINOP / MK_ONEUSE / MK_COMBINEOR: sub-matcher(s)
};
static Matcher CV_MPOOL[128];
static int CV_MPOS;
static Matcher *cv_m(int kind) { Matcher *m = &CV_MPOOL[CV_MPOS++]; *m = Matcher{}; m->kind = kind; return m; }

inline Matcher *m_Value()                { return cv_m(MK_VALUE); }   // matches anything, binds nothing
inline Matcher *m_Value(Value &v)        { Matcher *m = cv_m(MK_VALUE); m->cap = &v; return m; }
inline Matcher *m_Value(Value *&v)       { Matcher *m = cv_m(MK_VALUE); m->capp = &v; return m; }
inline Matcher *m_ConstantInt(Value &v)  { Matcher *m = cv_m(MK_CONSTANT); m->cap = &v; return m; }
inline Matcher *m_Specific(const Value &v){ Matcher *m = cv_m(MK_SPECIFIC); m->specific = &v; return m; }
inline Matcher *m_Zero()                 { return cv_m(MK_ZERO); }
inline Matcher *m_One()                  { return cv_m(MK_ONE); }
inline Matcher *m_AllOnes()              { return cv_m(MK_ALLONES); }
inline Matcher *m_SpecificInt(unsigned long n) { Matcher *m = cv_m(MK_SPECIFICINT); m->imm = n; return m; }
// m_BinOp(LHS, RHS) matches ANY binary operator whose operands match -- upstream uses it where the
// opcode is already fixed by the caller (foldAndToXor asserts `I` is an `and`), so constraining the
// opcode here would be stricter than the real matcher and would silently skip the arm. `icmp` is
// deliberately NOT a BinaryOperator in LLVM and is excluded here for the same reason.
// m_ICmp(Pred, L, R) matches an icmp and BINDS its predicate. Only meaningful now that icmp is a
// modelled node with a predicate on it.
// m_SpecificInt_ICMP(Pred, Threshold) matches a CONSTANT operand C for which `C Pred Threshold`
// holds -- it is a constraint on the constant, not an icmp instruction to match.
inline Matcher *m_SpecificInt_ICMP(CVPredicate p, const APInt &threshold) {
  Matcher *m = cv_m(MK_CONSTCMP); m->pred = p; m->imm = threshold.v; m->imm_bits = threshold.bits;
  return m;
}
inline Matcher *m_ICmp(CVPredicate &p, Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_ICMP); m->a = a; m->b = b; m->pred_out = &p; return m;
}
inline Matcher *m_c_ICmp(CVPredicate &p, Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_ICMP); m->a = a; m->b = b; m->pred_out = &p; m->commutative = true; return m;
}
inline Matcher *m_BinOp(Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_ANYBINOP); m->a = a; m->b = b; return m;
}
inline Matcher *m_c_BinOp(Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_ANYBINOP); m->a = a; m->b = b; m->commutative = true; return m;
}
inline Matcher *m_OneUse(Matcher *inner) { Matcher *m = cv_m(MK_ONEUSE); m->a = inner; return m; }
// m_CombineAnd(P, Q): the value must satisfy BOTH sub-patterns -- upstream uses it to capture a
// node (m_Value) while ALSO constraining its shape in the same position.
inline Matcher *m_CombineAnd(Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_COMBINEAND); m->a = a; m->b = b; return m;
}

// --- the remaining matcher surface unmodified upstream folds name. `m_APInt`/`m_Constant` capture a
// --- constant operand (the shim's Value carries `is_const`), `m_Not`/`m_Neg` are the canonical
// --- sugar for `xor X, -1` / `sub 0, X`, and `m_Deferred` re-matches an already-bound value. These
// --- are STRUCTURAL: they let the fold's real matcher tree run, and any operand the executor cannot
// --- resolve simply fails the match on that path, as it would in LLVM.
inline Matcher *m_SpecificInt(const APInt &a) { Matcher *m = cv_m(MK_SPECIFICINT); m->imm = a.v; return m; }
inline Matcher *m_APInt(const APInt *&c)  { Matcher *m = cv_m(MK_CONSTANT); m->apint = &c; return m; }
inline Matcher *m_Constant(Value &v)      { Matcher *m = cv_m(MK_CONSTANT); m->cap = &v; return m; }
inline Matcher *m_Constant(Value *&v)     { Matcher *m = cv_m(MK_CONSTANT); m->capp = &v; return m; }
inline Matcher *m_ConstantInt(Value *&v)  { Matcher *m = cv_m(MK_CONSTANT); m->capp = &v; return m; }
// m_Deferred re-matches a value bound EARLIER IN THE SAME PATTERN, so it must read the binding at
// MATCH time. The matcher tree is fully constructed before `match()` runs, so snapshotting the
// pointer here captured a null: `(A & B) ^ (A | B)` -- the canonical shape foldXorToXor exists
// for -- dereferenced it and SEGFAULTED the harness. Store the binding's address instead.
// `AllowUndef` variants also match constants containing undef lanes. This shim does not model
// undef, so they are treated as the STRICT form: a constant with undef in it simply fails to match.
// That is narrower than upstream (an arm may go unexplored) and never wider, so it cannot admit a
// path upstream would not take.
inline Matcher *m_APIntAllowUndef(const APInt *&c) { return m_APInt(c); }
inline Matcher *m_SpecificIntAllowUndef(unsigned long n) { return m_SpecificInt(n); }
inline Matcher *m_SpecificIntAllowUndef(const APInt &a) { return m_SpecificInt(a); }
inline Matcher *m_Deferred(Value *&v)     { Matcher *m = cv_m(MK_DEFERRED); m->deferred = &v; return m; }
inline Matcher *m_Specific(Value *v)      { Matcher *m = cv_m(MK_SPECIFIC); m->specific = v; return m; }
inline Matcher *m_ImmConstant(Value &v)   { Matcher *m = cv_m(MK_CONSTANT); m->cap = &v; return m; }
inline Matcher *m_ImmConstant(Value *&v)  { Matcher *m = cv_m(MK_CONSTANT); m->capp = &v; return m; }
inline Matcher *m_Deferred(Value &v)      { Matcher *m = cv_m(MK_SPECIFIC); m->specific = &v; return m; }
inline Matcher *m_ZeroInt()               { return cv_m(MK_ZERO); }
static Matcher *cv_bin(int op, Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_BINOP); m->opcode = op; m->a = a; m->b = b; return m;
}
inline Matcher *m_Not(Matcher *inner)     { return cv_bin(OP_XOR, inner, m_AllOnes()); }
inline Matcher *m_Neg(Matcher *inner)     { return cv_bin(OP_SUB, m_Zero(), inner); }
inline Matcher *m_Add(Matcher *a, Matcher *b)  { return cv_bin(OP_ADD, a, b); }
inline Matcher *m_Sub(Matcher *a, Matcher *b)  { return cv_bin(OP_SUB, a, b); }
inline Matcher *m_Mul(Matcher *a, Matcher *b)  { return cv_bin(OP_MUL, a, b); }
inline Matcher *m_And(Matcher *a, Matcher *b)  { return cv_bin(OP_AND, a, b); }
inline Matcher *m_Or(Matcher *a, Matcher *b)   { return cv_bin(OP_OR, a, b); }
inline Matcher *m_Xor(Matcher *a, Matcher *b)  { return cv_bin(OP_XOR, a, b); }
inline Matcher *m_Shl(Matcher *a, Matcher *b)  { return cv_bin(OP_SHL, a, b); }
inline Matcher *m_LShr(Matcher *a, Matcher *b) { return cv_bin(OP_LSHR, a, b); }
inline Matcher *m_AShr(Matcher *a, Matcher *b) { return cv_bin(OP_ASHR, a, b); }
inline Matcher *m_UDiv(Matcher *a, Matcher *b) { return cv_bin(OP_UDIV, a, b); }
inline Matcher *m_SDiv(Matcher *a, Matcher *b) { return cv_bin(OP_SDIV, a, b); }
inline Matcher *m_URem(Matcher *a, Matcher *b) { return cv_bin(OP_UREM, a, b); }
inline Matcher *m_SRem(Matcher *a, Matcher *b) { return cv_bin(OP_SREM, a, b); }
// commutative matchers (try both operand orders), as real InstCombine uses for +,*,&,|,^.
static Matcher *cv_cbin(int op, Matcher *a, Matcher *b) { Matcher *m = cv_bin(op, a, b); m->commutative = true; return m; }
inline Matcher *m_c_Add(Matcher *a, Matcher *b) { return cv_cbin(OP_ADD, a, b); }
inline Matcher *m_c_Mul(Matcher *a, Matcher *b) { return cv_cbin(OP_MUL, a, b); }
inline Matcher *m_c_And(Matcher *a, Matcher *b) { return cv_cbin(OP_AND, a, b); }
inline Matcher *m_c_Or(Matcher *a, Matcher *b)  { return cv_cbin(OP_OR, a, b); }
inline Matcher *m_c_Xor(Matcher *a, Matcher *b) { return cv_cbin(OP_XOR, a, b); }
inline Matcher *m_CombineOr(Matcher *a, Matcher *b) { Matcher *m = cv_m(MK_COMBINEOR); m->a = a; m->b = b; return m; }

static bool cv_matchV(const Value &v, Matcher *m) {
  switch (m->kind) {
    case MK_VALUE:                                       // capture any value
      // Bind the ACTUAL node, not a copy. LLVM's m_Value(A) binds the real `Value *`, and real folds
      // rely on that: `hasCommonOperand(A,B,C,D)` in foldNotXor tests `A == C` to detect a SHARED
      // operand. Binding a copy makes every such pointer-identity test false, so the fold silently
      // declines and the arm is never explored -- not unsound, but invisible non-modelling.
      if (m->capp) { *m->capp = const_cast<Value *>(&v); } else if (m->cap) { *m->cap = v; }
      return true;
    case MK_CONSTANT:                                    // a ConstantInt
      if (!v.is_const) return false;
      if (m->apint) {                                    // m_APInt: bind the VALUE, not just the node
        const APInt *a = cv_apint_of(v);
        if (!a) return false;                            // unrecognised constant spelling -> no match
        *m->apint = a;
      }
      if (m->capp) { *m->capp = cv_keep(v); } else if (m->cap) { *m->cap = v; }
      return true;
    case MK_SPECIFIC: return m->specific && v.t == m->specific->t;   // the same value (by term)
    case MK_DEFERRED:                                    // whatever the earlier m_Value bound
      return m->deferred && *m->deferred && v.t == (*m->deferred)->t;
    case MK_ZERO:     return v.t == "(_ bv0 32)";
    case MK_ONE:      return v.t == "(_ bv1 32)";
    case MK_ALLONES:  return v.t == "(_ bv4294967295 32)";
    case MK_SPECIFICINT: return v.t == ("(_ bv" + std::to_string(m->imm) + " 32)");
    case MK_ONEUSE:   return v.one_use && cv_matchV(v, m->a);   // single-use profitability guard
    case MK_COMBINEOR: return cv_matchV(v, m->a) || cv_matchV(v, m->b);  // either pattern
    case MK_COMBINEAND: return cv_matchV(v, m->a) && cv_matchV(v, m->b);  // both patterns
    case MK_CONSTCMP: {                                  // a constant C with `C Pred Threshold`
      if (!v.is_const) return false;
      const APInt *c = cv_apint_of(v);
      if (!c) return false;
      unsigned long C = c->v, T = m->imm;
      long sc = (long)(int)C, st = (long)(int)T;         // 32-bit signed views for the S-predicates
      switch (m->pred) {
        case ICMP_EQ:  return C == T;   case ICMP_NE:  return C != T;
        case ICMP_ULT: return C <  T;   case ICMP_ULE: return C <= T;
        case ICMP_UGT: return C >  T;   case ICMP_UGE: return C >= T;
        case ICMP_SLT: return sc <  st; case ICMP_SLE: return sc <= st;
        case ICMP_SGT: return sc >  st; case ICMP_SGE: return sc >= st;
        default: cv_unsupported("m_SpecificInt_ICMP with an unmodelled predicate");
      }
    }
    case MK_ICMP:                                        // an icmp; binds its predicate
      if (v.opcode != OP_ICMP || !v.op0 || !v.op1) return false;
      if (cv_matchV(*v.op0, m->a) && cv_matchV(*v.op1, m->b)) {
        if (m->pred_out) *m->pred_out = v.pred;
        return true;
      }
      if (m->commutative && cv_matchV(*v.op1, m->a) && cv_matchV(*v.op0, m->b)) {
        // operands matched SWAPPED, so the predicate the caller should see is the swapped one
        if (m->pred_out) *m->pred_out = cv_swap_pred(v.pred);
        return true;
      }
      return false;
    case MK_ANYBINOP:                                    // any BinaryOperator, operands must match
      if (v.opcode == OP_OTHER || v.opcode == OP_ICMP || !v.op0 || !v.op1) return false;
      if (cv_matchV(*v.op0, m->a) && cv_matchV(*v.op1, m->b)) return true;
      return m->commutative && cv_matchV(*v.op1, m->a) && cv_matchV(*v.op0, m->b);
    case MK_BINOP:
      if (v.opcode != m->opcode || !v.op0 || !v.op1) return false;
      if (cv_matchV(*v.op0, m->a) && cv_matchV(*v.op1, m->b)) return true;
      return m->commutative && cv_matchV(*v.op1, m->a) && cv_matchV(*v.op0, m->b);  // swapped
  }
  return false;
}
inline bool match(const Value &v, Matcher *m) { CV_MPOS = 0; return cv_matchV(v, m); }
// upstream writes `match(&I, ...)` and `match(Op0, ...)` -- the subject arrives as a POINTER
inline bool match(const Value *v, Matcher *m) { return v && match(*v, m); }

// --- build a symbolic input instruction / tree (operands live in a Value arena) ---------------
static Value CV_VARENA[64];
static int CV_VPOS;
inline Value *cv_node(int opcode, const char *term, Value *a, Value *b) {
  Value *v = &CV_VARENA[CV_VPOS++];
  v->t = term; v->opcode = opcode; v->op0 = a; v->op1 = b;
  return v;
}
typedef Value Instruction;

// --- analysis queries: choice points recorded for the driver to ground semantically ----------
inline bool cv_query(const char *name, Value v) {
  int c = cv_next_choice();
  CV_DECISIONS.push_back({name, v.t, c});
  return c != 0;
}
inline bool isKnownToBeAPowerOfTwo(Value P)  { return cv_query("power-of-two", P); }
// The upstream 4-argument form. `OrZero` ADMITS ZERO, which is a strictly weaker fact, so it is
// recorded as its own query -- grounding it as strict power-of-two would assume the value is
// non-zero when the caller established no such thing. The remaining arguments (depth, context
// instruction) do not change WHAT is established, only how hard LLVM looks for it.
inline bool isKnownToBeAPowerOfTwo(Value *P, bool OrZero, unsigned = 0, const Value * = nullptr) {
  return cv_query(OrZero ? "power-of-two-or-zero" : "power-of-two", *P);
}
inline bool isKnownToBeAPowerOfTwo(Value P, bool OrZero, unsigned = 0, const Value * = nullptr) {
  return cv_query(OrZero ? "power-of-two-or-zero" : "power-of-two", P);
}
inline bool isKnownNonZero(Value X)          { return cv_query("nonzero", X); }
inline bool isKnownNonNegative(Value X)      { return cv_query("nonneg", X); }
inline bool isKnownNegative(Value X)         { return cv_query("negative", X); }
/* MaskedValueIsZero(V, Mask) -- when true, (V & Mask) == 0. It takes the MASK, and the fact is
 * emitted directly as a constraint (like haveNoCommonBitsSet) because a two-operand fact cannot be
 * expressed by cv_query, which records a single term. The previous one-argument form dropped the
 * mask entirely AND recorded a query name nothing could ground, so the assumption silently vanished
 * from the path condition -- which does not permit a false PROOF (proving under fewer assumptions is
 * stronger) but does permit a spurious REFUTATION, on a counterexample the dropped fact excludes. */
inline bool MaskedValueIsZero(Value X, Value Mask) {
  int c = cv_next_choice();
  if (c) cv_constraint("(= (bvand " + X.t + " " + Mask.t + ") (_ bv0 32))");
  return c;
}

// --- emit the explored path as JSON: input term, output term (or null), decisions -------------
static std::string CV_INPUT_POISON = "false";    // the input's poison condition (default: never)
inline void cv_emit(const std::string &input, const Value *out) {
  printf("{\"input\":\"%s\",\"output\":%s%s%s,\"input_poison\":\"%s\",\"output_poison\":\"%s\",\"decisions\":[",
         input.c_str(), out ? "\"" : "", out ? out->t.c_str() : "null", out ? "\"" : "",
         CV_INPUT_POISON.c_str(), out ? out->poison.c_str() : "false");
  for (size_t i = 0; i < CV_DECISIONS.size(); i++)
    printf("%s{\"q\":\"%s\",\"arg\":\"%s\",\"v\":%d}", i ? "," : "",
           CV_DECISIONS[i].query.c_str(), CV_DECISIONS[i].arg.c_str(), CV_DECISIONS[i].val);
  printf("],\"constraints\":[");
  for (size_t i = 0; i < CV_CONS.size(); i++) printf("%s\"%s\"", i ? "," : "", CV_CONS[i].c_str());
  printf("],\"decls\":[");
  for (size_t i = 0; i < CV_DECLS.size(); i++) printf("%s\"%s\"", i ? "," : "", CV_DECLS[i].c_str());
  printf("],\"logic\":\"%s\"}\n", CV_LOGIC.c_str());
}
inline void cv_setup(int argc, char **argv) {    // argv: <fold> <choice0> <choice1> ...
  for (int i = 2; i < argc; i++) CV_CHOICES.push_back(atoi(argv[i]));
}
#endif
