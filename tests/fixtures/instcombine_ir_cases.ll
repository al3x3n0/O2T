; InstCombine translation-validation cases: each single-BB integer function is proved equivalent
; to its real `opt -passes=instcombine` output by translating both to SMT over the parameters.

; Identity cascade: add 0, mul 1, xor self, or -> folds to `ret %b`.
define i32 @cascade(i32 %a, i32 %b) {
  %x = add i32 %a, 0
  %y = mul i32 %x, 1
  %z = xor i32 %y, %y
  %w = or i32 %z, %b
  ret i32 %w
}

; sub self -> 0.
define i32 @sub_self(i32 %a) {
  %s = sub i32 %a, %a
  ret i32 %s
}

; and with all-ones is identity; shift pair; these fold but stay equivalent.
define i32 @mask_and_shift(i32 %a) {
  %m = and i32 %a, -1
  %h = lshr i32 %m, 0
  ret i32 %h
}

; select over an icmp -- InstCombine may rewrite the comparison/operands; stays equivalent.
define i32 @select_cmp(i32 %a, i32 %b) {
  %c = icmp slt i32 %a, %b
  %r = select i1 %c, i32 %a, i32 %b
  ret i32 %r
}

; zext/trunc round-trip on the low bits.
define i32 @zext_trunc(i32 %a) {
  %t = trunc i32 %a to i16
  %z = zext i16 %t to i32
  ret i32 %z
}
