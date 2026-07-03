; Rotated, multi-block loops (guard + body + latch + LCSSA exit) -- the canonical
; shape clang -O1 emits and the form that stresses line-regex IR miners (the
; recurrence is split across blocks, the live-out is an LCSSA phi, not the loop phi).
; The SCEV frontend (cv-mine-scev-loop) reads LLVM's own recurrence analysis, so block
; layout is irrelevant. strengthReduce: a loop-variant product i*c becomes a running
; add k (+= c); wrongStride bumps the running IV by d != c and must be refuted.

define i32 @rotSR_before(i32 %c, i32 %n) {
entry:
  %guard = icmp sgt i32 %n, 0
  br i1 %guard, label %loop, label %exit
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %t = mul i32 %i, %c
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %done = icmp eq i32 %i.next, %n
  br i1 %done, label %exit, label %loop
exit:
  %acc.lcssa = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  ret i32 %acc.lcssa
}

define i32 @rotSR_after(i32 %c, i32 %n) {
entry:
  %guard = icmp sgt i32 %n, 0
  br i1 %guard, label %loop, label %exit
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %k = phi i32 [ 0, %entry ], [ %k.next, %loop ]
  %acc.next = add i32 %acc, %k
  %k.next = add i32 %k, %c
  %i.next = add i32 %i, 1
  %done = icmp eq i32 %i.next, %n
  br i1 %done, label %exit, label %loop
exit:
  %acc.lcssa = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  ret i32 %acc.lcssa
}

define i32 @wrongStride_before(i32 %c, i32 %d, i32 %n) {
entry:
  %guard = icmp sgt i32 %n, 0
  br i1 %guard, label %loop, label %exit
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %t = mul i32 %i, %c
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %done = icmp eq i32 %i.next, %n
  br i1 %done, label %exit, label %loop
exit:
  %acc.lcssa = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  ret i32 %acc.lcssa
}

define i32 @wrongStride_after(i32 %c, i32 %d, i32 %n) {
entry:
  %guard = icmp sgt i32 %n, 0
  br i1 %guard, label %loop, label %exit
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %k = phi i32 [ 0, %entry ], [ %k.next, %loop ]
  %acc.next = add i32 %acc, %k
  %k.next = add i32 %k, %d
  %i.next = add i32 %i, 1
  %done = icmp eq i32 %i.next, %n
  br i1 %done, label %exit, label %loop
exit:
  %acc.lcssa = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  ret i32 %acc.lcssa
}
