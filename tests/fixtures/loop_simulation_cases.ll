; Simulation-relation loop equivalence: two loops with a DIFFERENT state shape proved equivalent
; for ALL trip counts via a non-positional relation R. `dup` carries a redundant second accumulator
; (3 loop-carried states) computed the same way as `base`'s single one (2 states); R relates them.

define i32 @base(i32 %x, i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ %x, %entry ], [ %acc.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %acc.n = add i32 %acc, %i
  %i.n = add i32 %i, 1
  br label %h
e:
  ret i32 %acc
}

define i32 @dup(i32 %x, i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ %x, %entry ], [ %acc.n, %b ]
  %acc2 = phi i32 [ %x, %entry ], [ %acc2.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %acc.n = add i32 %acc, %i
  %acc2.n = add i32 %acc2, %i
  %i.n = add i32 %i, 1
  br label %h
e:
  ret i32 %acc2
}

; STRENGTH REDUCTION: `withmul` multiplies the IV by 3 each iteration; `strred` replaces the
; multiply with an accumulator j that strides by 3 (so j == 3*i). The affine relation j == 3*i is
; inferred automatically and carries the proof for all trip counts -- pure equality cannot.
define i32 @withmul(i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ 0, %entry ], [ %acc.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %t = mul i32 %i, 3
  %acc.n = add i32 %acc, %t
  %i.n = add i32 %i, 1
  br label %h
e:
  ret i32 %acc
}

define i32 @strred(i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ 0, %entry ], [ %acc.n, %b ]
  %j = phi i32 [ 0, %entry ], [ %j.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %acc.n = add i32 %acc, %j
  %j.n = add i32 %j, 3
  %i.n = add i32 %i, 1
  br label %h
e:
  ret i32 %acc
}

; NON-UNIT-STRIDE strength reduction: the IV strides by 2 and the accumulator by 10, so the
; inferred coefficient is c = 10/2 = 5 (j == 5*i). Tests stride alignment, not just unit IVs.
define i32 @mul2(i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ 0, %entry ], [ %acc.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %t = mul i32 %i, 5
  %acc.n = add i32 %acc, %t
  %i.n = add i32 %i, 2
  br label %h
e:
  ret i32 %acc
}

define i32 @sr2(i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ 0, %entry ], [ %acc.n, %b ]
  %j = phi i32 [ 0, %entry ], [ %j.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %acc.n = add i32 %acc, %j
  %j.n = add i32 %j, 10
  %i.n = add i32 %i, 2
  br label %h
e:
  ret i32 %acc
}
