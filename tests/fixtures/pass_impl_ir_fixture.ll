declare i32 @helper(i32)

define i32 @pass_impl_ir_fixture(i32 %x, i1 %cond) {
entry:
  %a = add i32 %x, 1
  br i1 %cond, label %then, label %else

then:
  %b = call i32 @helper(i32 %a)
  br label %merge

else:
  %c = sub i32 %a, 1
  br label %merge

merge:
  %r = phi i32 [%b, %then], [%c, %else]
  ret i32 %r
}
