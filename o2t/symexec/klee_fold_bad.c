/* Teeth harness for KLEE-driven symexec: a single PLANTED under-guarded fold -- it rewrites
 * `urem X,P -> X & (P-1)` with NO power-of-two guard. KLEE finds the one rewriting path, which
 * established no facts, so the driver refutes it (the rewrite does not refine the input for all P).
 */
#include "klee/klee.h"
#include <stdio.h>

static char ARENA[4096];
static int APOS;
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
static Value And(Value a, Value b) { return V(cat("(bvand ", a.t, " ", b.t, ")")); }
static Value Sub(Value a, Value b) { return V(cat("(bvsub ", a.t, " ", b.t, ")")); }

int main(void) {
  /* a symbolic, otherwise-unused input keeps the harness honest under KLEE. */
  int dummy;
  klee_make_symbolic(&dummy, sizeof dummy, "dummy");
  Value X = V("X"), P = V("P");
  Value out = And(X, Sub(P, V("(_ bv1 32)")));   /* BUG: unconditional rewrite */
  printf("{\"opcode\":0,\"input\":\"(bvurem X P)\",\"output\":\"%s\",\"decisions\":[]}\n", out.t);
  return 0;
}
