; Diamond if-then-else shapes that SimplifyCFG converts to `select`. The cfg-shape contract
; proves each converted select is value-equivalent to the source merge-phi for all inputs.

define i32 @diamond(i1 %c, i32 %a, i32 %b) {
entry:
  br i1 %c, label %then, label %else
then:
  br label %merge
else:
  br label %merge
merge:
  %r = phi i32 [ %a, %then ], [ %b, %else ]
  ret i32 %r
}

define i32 @diamondSwapped(i1 %c, i32 %x, i32 %y) {
entry:
  br i1 %c, label %t, label %f
t:
  br label %m
f:
  br label %m
m:
  %r = phi i32 [ %y, %t ], [ %x, %f ]
  ret i32 %r
}
