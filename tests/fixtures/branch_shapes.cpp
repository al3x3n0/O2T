// Branch-SHAPE fixtures: fold functions whose C++ control flow encodes a CFG
// transform. cv-mine-shapes.py parses the if/else/return structure into a nested
// ite-tree and proves the <name>_before form equivalent to <name>_after (and NOT
// equivalent to <name>_unsound_after). isTrue(C) models a branch on C (C != 0).
namespace llvm {
struct Value {};
bool isTrue(Value *);
}  // namespace llvm
using namespace llvm;

// Nested-branch collapse: if(c1){ if(c2) a else b } else b  ==  if(c1 && c2) a else b
Value *nestedCollapse_before(Value *c1, Value *c2, Value *a, Value *b) {
  if (isTrue(c1)) {
    if (isTrue(c2)) {
      return a;
    }
    return b;
  }
  return b;
}
Value *nestedCollapse_after(Value *c1, Value *c2, Value *a, Value *b) {
  if (isTrue(c1) && isTrue(c2)) {
    return a;
  }
  return b;
}
// collapsing with || instead of && is UNSOUND
Value *nestedCollapse_unsound_after(Value *c1, Value *c2, Value *a, Value *b) {
  if (isTrue(c1) || isTrue(c2)) {
    return a;
  }
  return b;
}

// Identical-arm branch elimination: if(c) a else a  ==  a
Value *identicalArms_before(Value *c, Value *a) {
  if (isTrue(c)) {
    return a;
  }
  return a;
}
Value *identicalArms_after(Value *c, Value *a) {
  return a;
}

// LOOP shape: a counted accumulation unrolls to a closed form.
// acc=a; for i in 0..3: acc = acc + a   ==   a + a + a + a   (unroll + reassociate)
Value *loopSum_before(Value *a) {
  Value *acc = a;
  for (int i = 0; i < 3; ++i) {
    acc = acc + a;
  }
  return acc;
}
Value *loopSum_after(Value *a) {
  return a + a + a + a;
}
// LICM shape: the invariant a*b is recomputed each iteration but hoists out.
// acc=z; for i in 0..3: acc = acc + a*b   ==   z + a*b + a*b + a*b
Value *loopLicm_before(Value *a, Value *b, Value *z) {
  Value *acc = z;
  for (int i = 0; i < 3; ++i) {
    acc = acc + a * b;
  }
  return acc;
}
Value *loopLicm_after(Value *a, Value *b, Value *z) {
  return z + a * b + a * b + a * b;
}
// teeth: wrong unroll count (2 instead of 3) is not equivalent
Value *loopSum_unsound_after(Value *a) {
  return a + a + a;
}

// Quadratic recurrence: the per-iteration delta uses the loop index, so there is
// NO affine closed form -- the invariant synthesizer must honestly decline.
Value *loopQuadratic_recurrence(Value *a) {
  Value *acc = a;
  for (int i = 0; i < 3; ++i) {
    acc = acc + i;
  }
  return acc;
}
