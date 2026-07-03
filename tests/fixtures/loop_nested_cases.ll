; NESTED loop: outer (i over [0,n)) with an inner loop (j over [0,i)) that accumulates into acc.
; Proved equivalent COMPOSITIONALLY -- the inner loops are shown to define the same transition,
; then the outer loops are proved equivalent with the inner abstracted as one uninterpreted
; function INNER (a QF_UFBV query). So a change to the inner body that preserves its transition is
; accepted; an inconsistent inner change fails the inner check, an outer change fails the outer.

define i32 @nested(i32 %n) {
entry:
  br label %oh
oh:
  %i = phi i32 [ 0, %entry ], [ %i.n, %ol ]
  %acc = phi i32 [ 0, %entry ], [ %acc.o, %ol ]
  %og = icmp slt i32 %i, %n
  br i1 %og, label %ih, label %oe
ih:
  %j = phi i32 [ 0, %oh ], [ %j.n, %il ]
  %accn = phi i32 [ %acc, %oh ], [ %acc.i, %il ]
  %ig = icmp slt i32 %j, %i
  br i1 %ig, label %il, label %ie
il:
  %acc.i = add i32 %accn, %j
  %j.n = add i32 %j, 1
  br label %ih
ie:
  br label %ol
ol:
  %acc.o = phi i32 [ %accn, %ie ]
  %i.n = add i32 %i, 1
  br label %oh
oe:
  ret i32 %acc
}
