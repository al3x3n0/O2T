; UNBOUNDED loop-rotate cases: guard-at-top `while` loops. The real `opt -passes=loop-rotate`
; produces a do-while with a pre-guard + bottom guard + lcssa exit; we reconstruct a canonical
; guard-on-current model from the rotated IR, self-verify it against the emitted instructions, and
; prove it equivalent to the original for ALL trip counts (the permuted state is auto-related).

; acc += i.
define i32 @sumloop(i32 %x, i32 %n) {
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

; acc = 2*acc + i (a non-trivial recurrence).
define i32 @polyloop(i32 %x, i32 %n) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %b ]
  %acc = phi i32 [ %x, %entry ], [ %acc.n, %b ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %b, label %e
b:
  %t = mul i32 %acc, 2
  %acc.n = add i32 %t, %i
  %i.n = add i32 %i, 1
  br label %h
e:
  ret i32 %acc
}
