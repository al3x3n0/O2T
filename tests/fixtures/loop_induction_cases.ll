; UNBOUNDED loop equivalence cases: a loop with a foldable body. The real `opt -passes=instcombine`
; simplifies the body but keeps the loop structure; the induction prover then shows the original and
; the folded loop return the same value for ALL trip counts (init / guard / step / result all agree).

; Body has `+0` and `*1` identities instcombine removes; the recurrence is acc' = acc + i.
define i32 @loopfold(i32 %x, i32 %n) {
entry:
  br label %head
head:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %acc = phi i32 [ %x, %entry ], [ %acc.next, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %z = add i32 %acc, 0
  %m = mul i32 %z, 1
  %acc.next = add i32 %m, %i
  %i.next = add i32 %i, 1
  br label %head
exit:
  ret i32 %acc
}

; Two live loop-carried states with a redundant `+0` instcombine folds: s accumulates p, p grows.
; Both stay live (s uses p; result uses s), so the loop structure is preserved across the fold.
define i32 @twostate(i32 %x, i32 %y, i32 %n) {
entry:
  br label %head
head:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %s = phi i32 [ %x, %entry ], [ %s.next, %body ]
  %p = phi i32 [ %y, %entry ], [ %p.next, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %s0 = add i32 %s, 0
  %s.next = add i32 %s0, %p
  %p.next = add i32 %p, 1
  %i.next = add i32 %i, 1
  br label %head
exit:
  ret i32 %s
}
