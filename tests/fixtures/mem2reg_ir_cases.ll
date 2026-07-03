; Mem2Reg translation-validation cases: each promotes an `alloca` to SSA, building `phi` nodes.
; The real `opt -passes=mem2reg` output is proved to return the same value as the memory version,
; for all inputs and branch conditions, by symbolically executing both over the shared CFG.

; Diamond: the value stored on the taken arm is loaded at the merge -> phi [%x,%t],[%y,%e].
define i32 @diamond(i1 %c, i32 %x, i32 %y) {
entry:
  %p = alloca i32
  br i1 %c, label %t, label %e
t:
  store i32 %x, ptr %p
  br label %m
e:
  store i32 %y, ptr %p
  br label %m
m:
  %v = load i32, ptr %p
  ret i32 %v
}

; Straight-line store/load chain -> pure SSA (no phi needed).
define i32 @chain(i32 %x) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  %a = load i32, ptr %p
  %b = add i32 %a, 1
  store i32 %b, ptr %p
  %r = load i32, ptr %p
  ret i32 %r
}

; A store before the branch reaches the merge on the not-taken path -> phi [%d,%t],[%x,%entry].
define i32 @partial(i1 %c, i32 %x) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  br i1 %c, label %t, label %m
t:
  %d = add i32 %x, 5
  store i32 %d, ptr %p
  br label %m
m:
  %v = load i32, ptr %p
  ret i32 %v
}

; Nested diamonds -> a three-incoming phi [%x,%a],[%y,%b1],[%z,%b2].
define i32 @nested(i1 %c1, i1 %c2, i32 %x, i32 %y, i32 %z) {
entry:
  %p = alloca i32
  br i1 %c1, label %a, label %b
a:
  store i32 %x, ptr %p
  br label %j
b:
  br i1 %c2, label %b1, label %b2
b1:
  store i32 %y, ptr %p
  br label %j
b2:
  store i32 %z, ptr %p
  br label %j
j:
  %v = load i32, ptr %p
  ret i32 %v
}
