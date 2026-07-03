; Loop-CFG transform cases: each is a constant-trip-count loop. BOUNDED translation validation
; fully unrolls the original and the transformed loop and proves the acyclic forms equivalent, so
; the transform (loop-rotate / simple-loop-unswitch) is shown to preserve the computation for that
; trip count. (A non-constant trip count is not unrolled -> declined, never falsely proved.)

; A 4-iteration polynomial recurrence: acc = 2*acc + i.
define i32 @poly(i32 %x) {
entry:
  br label %head
head:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %acc = phi i32 [ %x, %entry ], [ %acc.next, %body ]
  %c = icmp slt i32 %i, 4
  br i1 %c, label %body, label %exit
body:
  %t = mul i32 %acc, 2
  %acc.next = add i32 %t, %i
  %i.next = add i32 %i, 1
  br label %head
exit:
  ret i32 %acc
}

; A loop with a loop-INVARIANT branch (%inv) -- the seed for unswitching.
define i32 @unswitchable(i32 %x, i1 %inv) {
entry:
  br label %head
head:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %acc = phi i32 [ %x, %entry ], [ %acc.next, %body ]
  %c = icmp slt i32 %i, 4
  br i1 %c, label %body, label %exit
body:
  %sel = select i1 %inv, i32 %acc, i32 %i
  %acc.next = add i32 %sel, 1
  %i.next = add i32 %i, 1
  br label %head
exit:
  ret i32 %acc
}
