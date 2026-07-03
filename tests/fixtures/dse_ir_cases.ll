; DSE translation-validation cases over ESCAPING memory (parameter pointers): a store removed by
; the real `opt -passes=dse` must be one a later store overwrites, so the final memory is
; preserved. Each function is proved equivalent to its real DSE output via a theory of arrays.

; A dead store overwritten by the next store to the same pointer -- DSE drops the first.
define i32 @dead_store(ptr %p) {
entry:
  store i32 1, ptr %p
  store i32 2, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}

; Two pointers: the first store to %p is overwritten by the third; the store to %q is live.
; DSE drops only the dead `store i32 1, ptr %p`; %q's store and the final %p store are preserved.
define i32 @two_pointer(ptr %p, ptr %q) {
entry:
  store i32 1, ptr %p
  store i32 2, ptr %q
  store i32 3, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}

; No dead store -- DSE leaves it unchanged (trivially equivalent).
define void @live_store(ptr %p, ptr %q) {
entry:
  store i32 7, ptr %p
  store i32 9, ptr %q
  ret void
}
