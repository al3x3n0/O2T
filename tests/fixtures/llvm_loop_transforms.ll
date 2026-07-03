; Before/after pairs of a LOOP OPTIMIZATION, as real LLVM IR. cv-mine-llvm-relational
; extracts each loop's PHI recurrence and proves the transform via a synthesized
; simulation relation, for all trip counts. (SSA phi loops are parallel, so no
; update-ordering subtlety.)

; Strength reduction: `acc += i*c` (a multiply each iteration) becomes a running
; induction variable `acc += k; k += c`. Simulation relation: { k == c*i, acc == acc }.
define i32 @strengthReduce_before(i32 %c, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %t = mul i32 %i, %c
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %cc = icmp slt i32 %i.next, %n
  br i1 %cc, label %loop, label %exit
exit:
  ret i32 %acc
}
define i32 @strengthReduce_after(i32 %c, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %k = phi i32 [ 0, %entry ], [ %k.next, %loop ]
  %acc.next = add i32 %acc, %k
  %k.next = add i32 %k, %c
  %i.next = add i32 %i, 1
  %cc = icmp slt i32 %i.next, %n
  br i1 %cc, label %loop, label %exit
exit:
  ret i32 %acc
}

; Wrong stride: the running IV is bumped by %d, not %c -- NOT equivalent (must refute).
define i32 @wrongStride_before(i32 %c, i32 %d, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %t = mul i32 %i, %c
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %cc = icmp slt i32 %i.next, %n
  br i1 %cc, label %loop, label %exit
exit:
  ret i32 %acc
}
define i32 @wrongStride_after(i32 %c, i32 %d, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %k = phi i32 [ 0, %entry ], [ %k.next, %loop ]
  %acc.next = add i32 %acc, %k
  %k.next = add i32 %k, %d
  %i.next = add i32 %i, 1
  %cc = icmp slt i32 %i.next, %n
  br i1 %cc, label %loop, label %exit
exit:
  ret i32 %acc
}
