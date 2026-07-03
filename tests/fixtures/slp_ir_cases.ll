; SLP translation-validation cases: a bundle of scalar loads/ops/stores that the real
; `opt -passes=slp-vectorizer` turns into a vector load / vector op / (shuffle) / vector store.
; Each is proved equivalent by modeling memory as compile-time cells and matching per-cell stores.
; The x86 target is required for the SLP cost model to find vectorization profitable.
target datalayout = "e-m:e-i64:64-f80:128-n8:16:32:64-S128"
target triple = "x86_64-unknown-linux-gnu"

; c[i] = a[i] + b[i] -> a single <4 x i32> load/add/store.
define void @vadd(ptr noalias %a, ptr noalias %b, ptr noalias %c) {
  %a1 = getelementptr i32, ptr %a, i64 1
  %a2 = getelementptr i32, ptr %a, i64 2
  %a3 = getelementptr i32, ptr %a, i64 3
  %b1 = getelementptr i32, ptr %b, i64 1
  %b2 = getelementptr i32, ptr %b, i64 2
  %b3 = getelementptr i32, ptr %b, i64 3
  %c1 = getelementptr i32, ptr %c, i64 1
  %c2 = getelementptr i32, ptr %c, i64 2
  %c3 = getelementptr i32, ptr %c, i64 3
  %la0 = load i32, ptr %a
  %la1 = load i32, ptr %a1
  %la2 = load i32, ptr %a2
  %la3 = load i32, ptr %a3
  %lb0 = load i32, ptr %b
  %lb1 = load i32, ptr %b1
  %lb2 = load i32, ptr %b2
  %lb3 = load i32, ptr %b3
  %r0 = add i32 %la0, %lb0
  %r1 = add i32 %la1, %lb1
  %r2 = add i32 %la2, %lb2
  %r3 = add i32 %la3, %lb3
  store i32 %r0, ptr %c
  store i32 %r1, ptr %c1
  store i32 %r2, ptr %c2
  store i32 %r3, ptr %c3
  ret void
}

; c[i] = a[3-i] * b[3-i] -> a vector mul followed by a reversing shufflevector <3,2,1,0>.
define void @vrev(ptr noalias %a, ptr noalias %b, ptr noalias %c) {
  %a1 = getelementptr i32, ptr %a, i64 1
  %a2 = getelementptr i32, ptr %a, i64 2
  %a3 = getelementptr i32, ptr %a, i64 3
  %b1 = getelementptr i32, ptr %b, i64 1
  %b2 = getelementptr i32, ptr %b, i64 2
  %b3 = getelementptr i32, ptr %b, i64 3
  %c1 = getelementptr i32, ptr %c, i64 1
  %c2 = getelementptr i32, ptr %c, i64 2
  %c3 = getelementptr i32, ptr %c, i64 3
  %la0 = load i32, ptr %a
  %la1 = load i32, ptr %a1
  %la2 = load i32, ptr %a2
  %la3 = load i32, ptr %a3
  %lb0 = load i32, ptr %b
  %lb1 = load i32, ptr %b1
  %lb2 = load i32, ptr %b2
  %lb3 = load i32, ptr %b3
  %r0 = mul i32 %la0, %lb0
  %r1 = mul i32 %la1, %lb1
  %r2 = mul i32 %la2, %lb2
  %r3 = mul i32 %la3, %lb3
  store i32 %r3, ptr %c
  store i32 %r2, ptr %c1
  store i32 %r1, ptr %c2
  store i32 %r0, ptr %c3
  ret void
}
