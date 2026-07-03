; Canonical counted-loop forms, as LLVM opt/clang emit them. cv-mine-llvm-loop
; extracts the PHI-based recurrence (mini scalar evolution) and synthesizes the
; closed-form loop invariant for ALL trip counts.

; acc += a  ->  acc == a*i
define i32 @sum_const(i32 %a, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %acc.next = add i32 %acc, %a
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, %n
  br i1 %c, label %loop, label %exit
exit:
  ret i32 %acc
}

; acc += i  (triangular sum)  ->  2*acc == i*i - i
define i32 @triangular(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %acc.next = add i32 %acc, %i
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, %n
  br i1 %c, label %loop, label %exit
exit:
  ret i32 %acc
}

; acc += i*c  ->  2*acc == c*i*i - c*i  (strength-reducible)
define i32 @scaled_triangular(i32 %c, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %t = mul i32 %i, %c
  %acc.next = add i32 %acc, %t
  %i.next = add i32 %i, 1
  %c2 = icmp slt i32 %i.next, %n
  br i1 %c2, label %loop, label %exit
exit:
  ret i32 %acc
}

; acc += i*i (sum of squares)  ->  6*acc == 2*i*i*i - 3*i*i + i  (CUBIC)
define i32 @sum_squares(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %sq = mul i32 %i, %i
  %acc.next = add i32 %acc, %sq
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, %n
  br i1 %c, label %loop, label %exit
exit:
  ret i32 %acc
}
