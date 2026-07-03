; Value-preserving scalar passes (reassociate / early-cse / gvn / instsimplify) must keep each
; function's returned value; the real `opt -passes=<P>` output is proved equivalent input by input.

; (a+b+c) - (c+a+b) == 0 -- reassociate proves this folds to 0.
define i32 @cancel(i32 %a, i32 %b, i32 %c) {
  %t1 = add i32 %a, %b
  %t2 = add i32 %t1, %c
  %t3 = add i32 %c, %a
  %t4 = add i32 %t3, %b
  %r = sub i32 %t2, %t4
  ret i32 %r
}

; redundant subexpression: x and y are a*b; early-cse/gvn collapse them.
define i32 @redundant(i32 %a, i32 %b) {
  %x = mul i32 %a, %b
  %y = mul i32 %a, %b
  %r = add i32 %x, %y
  ret i32 %r
}

; a chain that reassociate restructures but preserves: ((a+b)+c)+d.
define i32 @chain(i32 %a, i32 %b, i32 %c, i32 %d) {
  %s1 = add i32 %a, %b
  %s2 = add i32 %s1, %c
  %s3 = add i32 %s2, %d
  ret i32 %s3
}
