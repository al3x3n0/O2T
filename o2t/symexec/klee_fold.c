/* KLEE-driven symbolic execution of real fold control flow.
 *
 * A C-style "symbolic LLVM" harness (no STL, so KLEE runs cleanly): each Value is an SMT term in a
 * bump arena; builder calls build output terms; analysis queries are made SYMBOLIC via
 * klee_make_symbolic so KLEE FORKS on them. The input opcode is ALSO symbolic, so KLEE explores
 * the cross-product of input shape x guard outcomes -- the feasible control-flow paths of the
 * dispatcher -- automatically. KLEE writes one test case per path; replaying each (libkleeRuntest)
 * reproduces that path concretely and prints {opcode, input, output, decisions} for the driver to
 * discharge `(facts the branches established) => out == in`.
 */
#include "klee/klee.h"
#include <stdio.h>

static char ARENA[16384];
static int APOS;
/* manual string concatenation into the arena -- no libc string/varargs (KLEE-friendly). */
static const char *cat(const char *a, const char *b, const char *c, const char *d, const char *e) {
  const char *parts[5] = {a, b, c, d, e};
  char *p = ARENA + APOS;
  int n = 0;
  for (int k = 0; k < 5; k++)
    if (parts[k])
      for (const char *s = parts[k]; *s; s++) p[n++] = *s;
  p[n++] = 0;
  APOS += n;
  return p;
}

typedef struct { const char *t; } Value;
static Value V(const char *t) { Value v; v.t = t; return v; }

/* IRBuilder */
static Value And(Value a, Value b)  { return V(cat("(bvand ", a.t, " ", b.t, ")")); }
static Value Sub(Value a, Value b)  { return V(cat("(bvsub ", a.t, " ", b.t, ")")); }
static Value UDiv(Value a, Value b) { return V(cat("(bvudiv ", a.t, " ", b.t, ")")); }
static Value One(void)              { return V("(_ bv1 32)"); }

/* analysis queries -> symbolic choice points recorded for the driver */
struct Dec { const char *q; const char *arg; int v; };
static struct Dec DEC[16];
static int NDEC;
static int query(const char *name, Value v) {
  int c;
  klee_make_symbolic(&c, sizeof c, name);
  c &= 1;
  DEC[NDEC].q = name; DEC[NDEC].arg = v.t; DEC[NDEC].v = c; NDEC++;
  return c;
}
static int isKnownToBeAPowerOfTwo(Value P) { return query("power-of-two", P); }
static int isKnownNonNegative(Value X)     { return query("nonneg", X); }

/* the dispatcher -- the REAL guard structure of two folds (sound). */
enum { OP_UREM, OP_SDIV, OP_N };
static Value runFold(int opcode, Value X, Value P) {
  if (opcode == OP_UREM) {                 /* urem X,P -> X & (P-1), guarded by power-of-two */
    if (isKnownToBeAPowerOfTwo(P)) return And(X, Sub(P, One()));
    return V("");
  }
  if (opcode == OP_SDIV) {                  /* sdiv X,P -> udiv X,P, guarded by non-negativity */
    if (isKnownNonNegative(X) && isKnownNonNegative(P)) return UDiv(X, P);
    return V("");
  }
  return V("");
}

static const char *input_term(int opcode) {
  if (opcode == OP_SDIV) return "(bvsdiv X P)";
  return "(bvurem X P)";                    /* urem and badurem share the urem input */
}

int main(void) {
  int opcode;
  klee_make_symbolic(&opcode, sizeof opcode, "opcode");
  klee_assume(opcode >= 0);
  klee_assume(opcode < OP_N);
  Value out = runFold(opcode, V("X"), V("P"));
  printf("{\"opcode\":%d,\"input\":\"%s\",\"output\":%s%s%s,\"decisions\":[",
         opcode, input_term(opcode), out.t[0] ? "\"" : "", out.t[0] ? out.t : "null",
         out.t[0] ? "\"" : "");
  for (int i = 0; i < NDEC; i++)
    printf("%s{\"q\":\"%s\",\"arg\":\"%s\",\"v\":%d}", i ? "," : "", DEC[i].q, DEC[i].arg, DEC[i].v);
  printf("]}\n");
  return 0;
}
