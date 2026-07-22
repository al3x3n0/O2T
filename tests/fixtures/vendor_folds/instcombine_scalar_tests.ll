; Verbatim single-BB scalar functions from LLVM 18 test/Transforms/InstCombine/{and,or,xor,add}.ll
; (llvmorg-18.1.8, Apache-2.0 WITH LLVM-exception). Signatures preserved; only renamed unique and
; FileCheck comments stripped. Whole-function TV: opt -passes=instcombine transforms each and O2T
; proves the whole transform sound (o2t/validate/corpus_tv.py).

; from InstCombine/and.ll  (@test1)
define i32 @and_test1(i32 %A) {
  %B = and i32 %A, 0
  ret i32 %B
}

; from InstCombine/and.ll  (@test2)
define i32 @and_test2(i32 %A) {
  %B = and i32 %A, -1
  ret i32 %B
}

; from InstCombine/and.ll  (@test3)
define i1 @and_test3(i1 %A) {
  %B = and i1 %A, false
  ret i1 %B
}

; from InstCombine/and.ll  (@test3_logical)
define i1 @and_test3_logical(i1 %A) {
  %B = select i1 %A, i1 false, i1 false
  ret i1 %B
}

; from InstCombine/and.ll  (@test4)
define i1 @and_test4(i1 %A) {
  %B = and i1 %A, true
  ret i1 %B
}

; from InstCombine/and.ll  (@test4_logical)
define i1 @and_test4_logical(i1 %A) {
  %B = select i1 %A, i1 true, i1 false
  ret i1 %B
}

; from InstCombine/and.ll  (@test5)
define i32 @and_test5(i32 %A) {
  %B = and i32 %A, %A
  ret i32 %B
}

; from InstCombine/and.ll  (@test6)
define i1 @and_test6(i1 %A) {
  %B = and i1 %A, %A
  ret i1 %B
}

; from InstCombine/and.ll  (@test6_logical)
define i1 @and_test6_logical(i1 %A) {
  %B = select i1 %A, i1 %A, i1 false
  ret i1 %B
}

; from InstCombine/and.ll  (@test7)
define i32 @and_test7(i32 %A) {
  %NotA = xor i32 %A, -1
  %B = and i32 %A, %NotA
  ret i32 %B
}

; from InstCombine/and.ll  (@test8)
define i8 @and_test8(i8 %A) {
  %B = and i8 %A, 3
  %C = and i8 %B, 4
  ret i8 %C
}

; from InstCombine/and.ll  (@test9)
define i1 @and_test9(i32 %A) {
  %B = and i32 %A, -2147483648
  %C = icmp ne i32 %B, 0
  ret i1 %C
}

; from InstCombine/and.ll  (@test9a)
define i1 @and_test9a(i32 %A) {
  %B = and i32 %A, -2147483648
  %C = icmp ne i32 %B, 0
  ret i1 %C
}

; from InstCombine/and.ll  (@test10)
define i32 @and_test10(i32 %A) {
  %B = and i32 %A, 12
  %C = xor i32 %B, 15
  %D = and i32 %C, 1
  ret i32 %D
}

