; Source loops for CLOSED-LOOP TRANSLATION VALIDATION: cv-translation-validate runs a REAL
; LLVM pass (opt -passes=X) on these functions and proves opt's actual output equivalent to
; the input for all trip counts (via the SCEV frontend + relational prover). This validates
; LLVM itself -- not a hand-written before/after pair. llvm-as-validated.

; A loop-variant product accumulated -- loop-reduce/licm/rotate keep it a loop (loop->loop
; mode), indvars computes its closed form and DELETES the accumulator (loop->closed-form mode).
define i32 @sumProduct(i32 %c, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %t = mul i32 %i, %c
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}

; A constant-multiplier delta (acc += i*2): instcombine rewrites i*2 -> i<<1, gvn/reassociate
; simplify, yet SCEV normalizes both sides to {0,+,2} so the formal tier proves equivalence.
define i32 @shiftMul(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %d = mul i32 %i, 2
  %acc.next = add i32 %acc, %d
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}

; An affine-combination delta (acc += a*i + b): the closed form 2*acc == a*i^2 + (2b-a)*i has a
; coefficient (2b-a) that is a linear combination of params -- proved by the integer-linear
; coefficient basis in the coupled synthesizer.
define i32 @affineCombo(i32 %a, i32 %b, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %ai = mul i32 %a, %i
  %t = add i32 %ai, %b
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}

; A simple affine accumulation (acc += c each step).
define i32 @sumConst(i32 %c, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %acc.next = add i32 %acc, %c
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}

; A shl-based delta (acc += i<<1): the formal resolver models only mul/add, so it
; DECLINES the shl and falls back to the semi-formal differential (§6a) -- honest decline.
define i32 @shiftLeft(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %d = shl i32 %i, 1
  %acc.next = add i32 %acc, %d
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}

; A QUADRATIC delta (acc += i*i) -> CUBIC closed form. indvars emits a 3-factor product
; widening plus a modular-inverse magic constant; O2T proves it formally via the Faulhaber
; closed form (polyquot) + N-ary widening lemmas, discharged mod 2^32.
define i32 @sumSquares(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %sq = mul i32 %i, %i
  %acc.next = add i32 %acc, %sq
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}

; A CUBIC delta (acc += i*i*i) -> QUARTIC closed form. indvars emits a 4-factor product
; widening /u 8 in i35 (plus the 2- and 3-factor ones); O2T proves it formally via the
; degree-3 Faulhaber closed form and the N-ary widening lemmas.
define i32 @sumCubes(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %i2 = mul i32 %i, %i
  %i3 = mul i32 %i2, %i
  %acc.next = add i32 %acc, %i3
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %acc
}
