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
#include <cstdio>
#include <cstdlib>

// a symbolic SSA value = its SMT term, plus optional instruction structure (opcode + operands) so
// NESTED PatternMatch (m_Add(m_Mul(...), ...)) can recurse, the way real LLVM matchers do.
struct Value {
  std::string t;
  int opcode = 0;                                // 0 == a leaf / non-instruction value
  Value *op0 = nullptr, *op1 = nullptr;
  bool is_const = false;                         // a ConstantInt (for isa/m_ConstantInt)
  bool one_use = true;                           // single-use (profitability guards)
  std::string poison = "false";                  // SMT bool: when this value is poison (UB modeling)
};

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
struct IRBuilder {
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
  // `freeze` stops poison propagation: the result is ALWAYS defined (poison -> an arbitrary fixed
  // value). The standard way to make a speculation poison-safe.
  Value CreateFreeze(Value a) { Value r; r.t = a.t; r.poison = "false"; return r; }
  // fast-math `fadd nnan X, Y`: the nnan flag asserts the result is never NaN; it is poison when the
  // sum actually IS NaN (e.g. +inf + -inf). The FP analogue of nsw -- a flag the pass must justify.
  Value CreateFAddNNan(Value x, Value y) {
    Value r; r.t = "(fp.add RNE " + x.t + " " + y.t + ")";
    r.poison = "(fp.isNaN (fp.add RNE " + x.t + " " + y.t + "))"; return r;
  }
};
/* ConstantInt::get(...) -- the real LLVM constant factory; the result `isa<ConstantInt>`. */
struct ConstantInt {
  static Value get(unsigned long v) { Value r = cv_bv(v); r.is_const = true; return r; }
};
struct BinaryOperator {};                         /* tag types for isa<>/dyn_cast<> */
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
enum CvOpcode { OP_OTHER, OP_ADD, OP_SUB, OP_MUL, OP_AND, OP_OR, OP_XOR,
                OP_SHL, OP_LSHR, OP_ASHR, OP_UDIV, OP_SDIV, OP_UREM, OP_SREM };

enum CvMKind { MK_VALUE, MK_SPECIFIC, MK_CONSTANT, MK_ZERO, MK_ONE, MK_ALLONES, MK_BINOP,
               MK_SPECIFICINT, MK_ONEUSE, MK_COMBINEOR };
struct Matcher {
  int kind, opcode;
  Value *cap;                                    // MK_VALUE / MK_CONSTANT: where to store
  const Value *specific;                         // MK_SPECIFIC: the value to compare against
  unsigned long imm;                             // MK_SPECIFICINT: the constant to match
  bool commutative = false;                       // MK_BINOP: try both operand orders (m_c_*)
  Matcher *a, *b;                                // MK_BINOP / MK_ONEUSE / MK_COMBINEOR: sub-matcher(s)
};
static Matcher CV_MPOOL[128];
static int CV_MPOS;
static Matcher *cv_m(int kind) { Matcher *m = &CV_MPOOL[CV_MPOS++]; *m = Matcher{}; m->kind = kind; return m; }

inline Matcher *m_Value(Value &v)        { Matcher *m = cv_m(MK_VALUE); m->cap = &v; return m; }
inline Matcher *m_ConstantInt(Value &v)  { Matcher *m = cv_m(MK_CONSTANT); m->cap = &v; return m; }
inline Matcher *m_Specific(const Value &v){ Matcher *m = cv_m(MK_SPECIFIC); m->specific = &v; return m; }
inline Matcher *m_Zero()                 { return cv_m(MK_ZERO); }
inline Matcher *m_One()                  { return cv_m(MK_ONE); }
inline Matcher *m_AllOnes()              { return cv_m(MK_ALLONES); }
inline Matcher *m_SpecificInt(unsigned long n) { Matcher *m = cv_m(MK_SPECIFICINT); m->imm = n; return m; }
inline Matcher *m_OneUse(Matcher *inner) { Matcher *m = cv_m(MK_ONEUSE); m->a = inner; return m; }
static Matcher *cv_bin(int op, Matcher *a, Matcher *b) {
  Matcher *m = cv_m(MK_BINOP); m->opcode = op; m->a = a; m->b = b; return m;
}
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
    case MK_VALUE:    *m->cap = v; return true;          // capture any value
    case MK_CONSTANT: if (!v.is_const) return false; *m->cap = v; return true;  // a ConstantInt
    case MK_SPECIFIC: return v.t == m->specific->t;       // the same value (by term)
    case MK_ZERO:     return v.t == "(_ bv0 32)";
    case MK_ONE:      return v.t == "(_ bv1 32)";
    case MK_ALLONES:  return v.t == "(_ bv4294967295 32)";
    case MK_SPECIFICINT: return v.t == ("(_ bv" + std::to_string(m->imm) + " 32)");
    case MK_ONEUSE:   return v.one_use && cv_matchV(v, m->a);   // single-use profitability guard
    case MK_COMBINEOR: return cv_matchV(v, m->a) || cv_matchV(v, m->b);  // either pattern
    case MK_BINOP:
      if (v.opcode != m->opcode || !v.op0 || !v.op1) return false;
      if (cv_matchV(*v.op0, m->a) && cv_matchV(*v.op1, m->b)) return true;
      return m->commutative && cv_matchV(*v.op1, m->a) && cv_matchV(*v.op0, m->b);  // swapped
  }
  return false;
}
inline bool match(const Value &v, Matcher *m) { CV_MPOS = 0; return cv_matchV(v, m); }

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
inline bool isKnownNonZero(Value X)          { return cv_query("nonzero", X); }
inline bool isKnownNonNegative(Value X)      { return cv_query("nonneg", X); }
inline bool isKnownNegative(Value X)         { return cv_query("negative", X); }
inline bool MaskedValueIsZero(Value X)       { return cv_query("masked-zero", X); }

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
