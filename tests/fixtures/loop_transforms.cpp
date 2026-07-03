// Before/after pairs of a LOOP OPTIMIZATION, parsed by cv-mine-shapes (text parser,
// no clang). cv-mine-relational extracts each loop's recurrence, prefixes A/B vars,
// and proves the transform via a synthesized simulation relation -- for all n.
// Params are loop-invariant inputs (shared consts); `i` is the loop index.
namespace llvm {
struct Value {};
}  // namespace llvm
using namespace llvm;

// Strength reduction: `acc += i*c` (a multiply each iteration) becomes a running
// induction variable `acc += k; k += c` (k == i*c). Simulation relation:
// { k == c*i, accA == accB }.
Value *strengthReduce_before(Value *c) {
  Value *acc = 0;
  for (int i = 0; i < 100; ++i) {
    acc = acc + i * c;
  }
  return acc;
}
Value *strengthReduce_after(Value *c) {
  Value *acc = 0;
  Value *k = 0;
  for (int i = 0; i < 100; ++i) {
    acc = acc + k;
    k = k + c;
  }
  return acc;
}

// Identity / no-op transform: same loop both sides (sanity that R = {acc==acc}).
Value *copyProp_before(Value *a) {
  Value *acc = 0;
  for (int i = 0; i < 100; ++i) {
    acc = acc + a;
  }
  return acc;
}
Value *copyProp_after(Value *a) {
  Value *acc = 0;
  for (int i = 0; i < 100; ++i) {
    acc = acc + a;
  }
  return acc;
}

// Order-dependent (sequential) bug: the CORRECT strength reduction reads the old k
// (`acc += k; k += c;`). Bumping k FIRST (`k += c; acc += k;`) makes acc see (i+1)*c
// -- an off-by-one. Only SEQUENTIAL body semantics distinguishes them; under parallel
// semantics both would falsely look equivalent. This pair must be REFUTED.
Value *wrongOrder_before(Value *c) {
  Value *acc = 0;
  for (int i = 0; i < 100; ++i) {
    acc = acc + i * c;
  }
  return acc;
}
Value *wrongOrder_after(Value *c) {
  Value *acc = 0;
  Value *k = 0;
  for (int i = 0; i < 100; ++i) {
    k = k + c;
    acc = acc + k;
  }
  return acc;
}

// MULTI-OUTPUT strength reduction: two multiplies -> two running IVs. Live-outs are
// designated by post-loop slot assigns (o0, o1). Proves the bijection
// {A_acc1<->B_acc1, A_acc2<->B_acc2} under {B_k1==a*i, B_k2==b*i}, for all n.
Value *multiSR_before(Value *a, Value *b) {
  Value *acc1 = 0;
  Value *acc2 = 0;
  for (int i = 0; i < 100; ++i) {
    acc1 = acc1 + i * a;
    acc2 = acc2 + i * b;
  }
  o0 = acc1;
  o1 = acc2;
  return acc1;
}
Value *multiSR_after(Value *a, Value *b) {
  Value *acc1 = 0;
  Value *k1 = 0;
  Value *acc2 = 0;
  Value *k2 = 0;
  for (int i = 0; i < 100; ++i) {
    acc1 = acc1 + k1;
    k1 = k1 + a;
    acc2 = acc2 + k2;
    k2 = k2 + b;
  }
  o0 = acc1;
  o1 = acc2;
  return acc1;
}
