; MULTI-EXIT loop: a header guard (exit when i >= n) plus an in-body break (exit when acc > lim).
; The model recovers both ordered exits and the continue-step; two such loops are proved equivalent
; for ALL trip counts by induction over (init, per-exit decision, per-exit result, step).

define i32 @search(i32 %n, i32 %lim) {
entry:
  br label %h
h:
  %i = phi i32 [ 0, %entry ], [ %i.n, %latch ]
  %acc = phi i32 [ 0, %entry ], [ %acc.n, %latch ]
  %g = icmp slt i32 %i, %n
  br i1 %g, label %body, label %exitN
body:
  %brk = icmp sgt i32 %acc, %lim
  br i1 %brk, label %exitB, label %latch
latch:
  %acc.n = add i32 %acc, %i
  %i.n = add i32 %i, 1
  br label %h
exitN:
  ret i32 %acc
exitB:
  ret i32 %acc
}
