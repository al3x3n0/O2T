// A tiny LLVM-like shim for bounded model checkers (CBMC/ESBMC).
//
// Unlike symbolic_llvm.h, Values here are concrete C++ bitvectors fed by nondet inputs. Analysis
// queries return nondet booleans and, when true, assume the semantic fact the real query would have
// established. Fold harnesses then assert the same poison-aware refinement property O2T discharges
// with SMT: where the input is defined, a rewrite must produce the same value and remain defined.
#ifndef CV_MODELCHECK_LLVM_H
#define CV_MODELCHECK_LLVM_H

#include <cstdint>
#include <cstdlib>
#include <limits>

#if defined(__CPROVER) || defined(__ESBMC__)
extern "C" unsigned int nondet_uint();
extern "C" bool nondet_bool();
#else
inline unsigned int nondet_uint() { return 0U; }
inline bool nondet_bool() { return false; }
#endif

#if defined(__CPROVER)
extern "C" void __CPROVER_assume(bool);
extern "C" void __CPROVER_assert(bool, const char *);
#define CV_ASSUME(cond) __CPROVER_assume(cond)
#define CV_ASSERT(cond, msg) __CPROVER_assert(cond, msg)
#elif defined(__ESBMC__)
extern "C" void __ESBMC_assume(bool);
extern "C" void __ESBMC_assert(bool, const char *);
#define CV_ASSUME(cond) __ESBMC_assume(cond)
#define CV_ASSERT(cond, msg) __ESBMC_assert(cond, msg)
#else
inline void cv_assume_fallback(bool cond) {
  if (!cond)
    std::abort();
}
inline void cv_assert_fallback(bool cond, const char *) {
  if (!cond)
    std::abort();
}
#define CV_ASSUME(cond) cv_assume_fallback(cond)
#define CV_ASSERT(cond, msg) cv_assert_fallback(cond, msg)
#endif

struct Value {
  uint64_t bits = 0;
  uint32_t width = 32;
  bool poison = false;
  bool valid = true;
};

inline uint64_t cv_mask(uint32_t width) {
  return width >= 64U ? std::numeric_limits<uint64_t>::max() : ((uint64_t{1} << width) - 1U);
}

inline uint64_t cv_normalize_bits(uint64_t bits, uint32_t width) {
  return bits & cv_mask(width);
}

inline Value cv_value_w(uint64_t bits, uint32_t width, bool poison = false) {
  return Value{cv_normalize_bits(bits, width), width, poison, true};
}

inline Value cv_value(uint32_t bits, bool poison = false) {
  return cv_value_w(bits, 32, poison);
}

inline Value cv_no_rewrite() {
  return Value{0U, 32, false, false};
}

inline uint64_t cv_nondet_u64() {
  uint64_t lo = (uint64_t)nondet_uint();
  uint64_t hi = (uint64_t)nondet_uint();
  return lo | (hi << 32U);
}

inline Value cv_any_bv(uint32_t width) {
  return cv_value_w(cv_nondet_u64(), width);
}

inline Value cv_any_poison_bv(uint32_t width) {
  return cv_value_w(cv_nondet_u64(), width, nondet_bool());
}

inline Value cv_any_i32() {
  return cv_any_bv(32);
}

inline Value cv_any_poison_i32() {
  return cv_any_poison_bv(32);
}

inline Value cv_any_i1() {
  return cv_any_bv(1);
}

inline Value cv_any_poison_i1() {
  return cv_any_poison_bv(1);
}

inline uint64_t cv_sign_bit(uint32_t width) {
  return uint64_t{1} << (width - 1U);
}

inline bool cv_negative(uint64_t value, uint32_t width) {
  return (cv_normalize_bits(value, width) & cv_sign_bit(width)) != 0U;
}

inline bool cv_slt_w(uint64_t a, uint64_t b, uint32_t width) {
  a = cv_normalize_bits(a, width);
  b = cv_normalize_bits(b, width);
  bool sa = cv_negative(a, width);
  bool sb = cv_negative(b, width);
  return sa != sb ? sa : a < b;
}

inline bool cv_sle_w(uint64_t a, uint64_t b, uint32_t width) {
  return cv_slt_w(a, b, width) || cv_normalize_bits(a, width) == cv_normalize_bits(b, width);
}

inline bool cv_sgt_w(uint64_t a, uint64_t b, uint32_t width) { return cv_slt_w(b, a, width); }
inline bool cv_sge_w(uint64_t a, uint64_t b, uint32_t width) { return cv_sle_w(b, a, width); }

inline int64_t cv_signed_to_i64(uint64_t value, uint32_t width) {
  value = cv_normalize_bits(value, width);
  if (width == 64U)
    return (int64_t)value;
  uint64_t sign = cv_sign_bit(width);
  uint64_t extended = (value & sign) ? (value | ~cv_mask(width)) : value;
  return (int64_t)extended;
}

inline bool cv_sadd_overflow(uint64_t a, uint64_t b, uint32_t width) {
  uint64_t result = cv_normalize_bits(a + b, width);
  bool sa = cv_negative(a, width);
  bool sb = cv_negative(b, width);
  bool sr = cv_negative(result, width);
  return sa == sb && sa != sr;
}

inline bool cv_ssub_overflow(uint64_t a, uint64_t b, uint32_t width) {
  uint64_t result = cv_normalize_bits(a - b, width);
  bool sa = cv_negative(a, width);
  bool sb = cv_negative(b, width);
  bool sr = cv_negative(result, width);
  return sa != sb && sr != sa;
}

inline bool cv_unsigned_mul_overflow(uint64_t a, uint64_t b, uint32_t width) {
  a = cv_normalize_bits(a, width);
  b = cv_normalize_bits(b, width);
  if (width <= 32U)
    return ((a * b) >> width) != 0U;
  if (a == 0U || b == 0U)
    return false;
  return a > (cv_mask(width) / b);
}

inline bool cv_signed_mul_overflow(uint64_t a, uint64_t b, uint32_t width) {
  if (width <= 32U) {
    int64_t sa = cv_signed_to_i64(a, width);
    int64_t sb = cv_signed_to_i64(b, width);
    int64_t product = sa * sb;
    int64_t min_value = -(int64_t)(uint64_t{1} << (width - 1U));
    int64_t max_value = (int64_t)((uint64_t{1} << (width - 1U)) - 1U);
    return product < min_value || product > max_value;
  }
  uint64_t result = cv_normalize_bits(a * b, width);
  if (a == 0U || b == 0U)
    return false;
  int64_t sa = cv_signed_to_i64(a, width);
  int64_t sb = cv_signed_to_i64(b, width);
  int64_t sr = cv_signed_to_i64(result, width);
  if (sa == -1) {
    if (b == cv_sign_bit(width))
      return true;
    return sr != -sb;
  }
  if (sb == -1) {
    if (a == cv_sign_bit(width))
      return true;
    return sr != -sa;
  }
  return sr / sb != sa;
}

inline bool cv_is_power_of_two(uint64_t v) {
  return v != 0U && (v & (v - 1U)) == 0U;
}

inline bool cv_slt(uint64_t a, uint64_t b) { return cv_slt_w(a, b, 32); }
inline bool cv_sle(uint64_t a, uint64_t b) { return cv_sle_w(a, b, 32); }
inline bool cv_sgt(uint64_t a, uint64_t b) { return cv_sgt_w(a, b, 32); }
inline bool cv_sge(uint64_t a, uint64_t b) { return cv_sge_w(a, b, 32); }

inline void cv_assert_refines(Value input, Value output, const char *msg) {
  if (output.valid)
    CV_ASSERT(input.poison || (input.width == output.width && !output.poison && output.bits == input.bits), msg);
}

inline void cv_assert_equivalent(Value input, Value output, const char *msg) {
  if (output.valid)
    CV_ASSERT(input.width == output.width && input.bits == output.bits && input.poison == output.poison, msg);
}

inline Value cv_poison(Value v) {
  v.poison = true;
  return v;
}

inline Value cv_freeze(Value v) {
  return cv_value_w(v.bits, v.width);
}

inline Value cv_bvnot(Value v) {
  return cv_value_w(~v.bits, v.width, v.poison);
}

inline Value cv_bvneg(Value v) {
  return cv_value_w(0U - v.bits, v.width, v.poison);
}

inline Value cv_bvadd(Value a, Value b, bool nsw = false, bool nuw = false) {
  uint64_t result = cv_normalize_bits(a.bits + b.bits, a.width);
  bool poison = a.poison || b.poison || a.width != b.width;
  if (nsw && a.width == b.width)
    poison = poison || cv_sadd_overflow(a.bits, b.bits, a.width);
  if (nuw && a.width == b.width)
    poison = poison || result < a.bits;
  return cv_value_w(result, a.width, poison);
}

inline Value cv_bvsub(Value a, Value b, bool nsw = false, bool nuw = false) {
  uint64_t result = cv_normalize_bits(a.bits - b.bits, a.width);
  bool poison = a.poison || b.poison || a.width != b.width;
  if (nsw && a.width == b.width)
    poison = poison || cv_ssub_overflow(a.bits, b.bits, a.width);
  if (nuw && a.width == b.width)
    poison = poison || a.bits < b.bits;
  return cv_value_w(result, a.width, poison);
}

inline Value cv_bvmul(Value a, Value b, bool nsw = false, bool nuw = false) {
  uint64_t result = cv_normalize_bits(a.bits * b.bits, a.width);
  bool poison = a.poison || b.poison || a.width != b.width;
  if (nuw && a.width == b.width)
    poison = poison || cv_unsigned_mul_overflow(a.bits, b.bits, a.width);
  if (nsw && a.width == b.width)
    poison = poison || cv_signed_mul_overflow(a.bits, b.bits, a.width);
  return cv_value_w(result, a.width, poison);
}

inline Value cv_bvand(Value a, Value b) {
  return cv_value_w(a.bits & b.bits, a.width, a.poison || b.poison || a.width != b.width);
}
inline Value cv_bvor(Value a, Value b) {
  return cv_value_w(a.bits | b.bits, a.width, a.poison || b.poison || a.width != b.width);
}
inline Value cv_bvxor(Value a, Value b) {
  return cv_value_w(a.bits ^ b.bits, a.width, a.poison || b.poison || a.width != b.width);
}

inline Value cv_bvshl(Value a, Value b, bool nsw = false, bool nuw = false) {
  bool shift_oob = b.bits >= a.width;
  uint64_t result = shift_oob ? 0U : cv_normalize_bits(a.bits << b.bits, a.width);
  bool poison = a.poison || b.poison || shift_oob;
  if (nuw && !shift_oob)
    poison = poison || cv_normalize_bits(result >> b.bits, a.width) != a.bits;
  if (nsw && !shift_oob)
    poison = poison || cv_normalize_bits(cv_signed_to_i64(result, a.width) >> b.bits, a.width) != a.bits;
  return cv_value_w(result, a.width, poison);
}

inline Value cv_bvlshr(Value a, Value b, bool exact = false) {
  bool shift_oob = b.bits >= a.width;
  uint64_t result = shift_oob ? 0U : (a.bits >> b.bits);
  bool poison = a.poison || b.poison || shift_oob;
  if (exact && !shift_oob)
    poison = poison || cv_normalize_bits(result << b.bits, a.width) != a.bits;
  return cv_value_w(result, a.width, poison);
}

inline Value cv_bvashr(Value a, Value b, bool exact = false) {
  bool shift_oob = b.bits >= a.width;
  uint64_t result = shift_oob ? 0U : cv_normalize_bits(cv_signed_to_i64(a.bits, a.width) >> b.bits, a.width);
  bool poison = a.poison || b.poison || shift_oob;
  if (exact && !shift_oob)
    poison = poison || cv_normalize_bits(result << b.bits, a.width) != a.bits;
  return cv_value_w(result, a.width, poison);
}

inline Value cv_bvudiv(Value a, Value b) {
  bool div_zero = b.bits == 0U;
  uint64_t result = div_zero ? 0U : (a.bits / b.bits);
  return cv_value_w(result, a.width, a.poison || b.poison || a.width != b.width || div_zero);
}

inline Value cv_bvurem(Value a, Value b) {
  bool div_zero = b.bits == 0U;
  uint64_t result = div_zero ? 0U : (a.bits % b.bits);
  return cv_value_w(result, a.width, a.poison || b.poison || a.width != b.width || div_zero);
}

inline Value cv_bvsdiv(Value a, Value b) {
  bool div_zero = b.bits == 0U;
  uint64_t min_value = cv_sign_bit(a.width);
  uint64_t minus_one = cv_mask(a.width);
  bool overflow = a.bits == min_value && b.bits == minus_one;
  int64_t dividend = cv_signed_to_i64(a.bits, a.width);
  int64_t divisor = cv_signed_to_i64(b.bits, b.width);
  uint64_t result = (div_zero || overflow) ? 0U : cv_normalize_bits((uint64_t)(dividend / divisor), a.width);
  return cv_value_w(result, a.width, a.poison || b.poison || a.width != b.width || div_zero || overflow);
}

inline Value cv_bvsrem(Value a, Value b) {
  bool div_zero = b.bits == 0U;
  uint64_t min_value = cv_sign_bit(a.width);
  uint64_t minus_one = cv_mask(a.width);
  bool overflow = a.bits == min_value && b.bits == minus_one;
  int64_t dividend = cv_signed_to_i64(a.bits, a.width);
  int64_t divisor = cv_signed_to_i64(b.bits, b.width);
  uint64_t result = (div_zero || overflow) ? 0U : cv_normalize_bits((uint64_t)(dividend % divisor), a.width);
  return cv_value_w(result, a.width, a.poison || b.poison || a.width != b.width || div_zero || overflow);
}

inline Value cv_bool(bool value, bool poison = false) {
  return cv_value_w(value ? 1U : 0U, 1, poison);
}

inline Value cv_eq(Value a, Value b) { return cv_bool(a.width == b.width && a.bits == b.bits, a.poison || b.poison); }
inline Value cv_ne(Value a, Value b) { return cv_bool(a.width != b.width || a.bits != b.bits, a.poison || b.poison); }
inline Value cv_bvslt(Value a, Value b) { return cv_bool(cv_slt_w(a.bits, b.bits, a.width), a.poison || b.poison || a.width != b.width); }
inline Value cv_bvsle(Value a, Value b) { return cv_bool(cv_sle_w(a.bits, b.bits, a.width), a.poison || b.poison || a.width != b.width); }
inline Value cv_bvsgt(Value a, Value b) { return cv_bool(cv_sgt_w(a.bits, b.bits, a.width), a.poison || b.poison || a.width != b.width); }
inline Value cv_bvsge(Value a, Value b) { return cv_bool(cv_sge_w(a.bits, b.bits, a.width), a.poison || b.poison || a.width != b.width); }
inline Value cv_bvult(Value a, Value b) { return cv_bool(a.bits < b.bits, a.poison || b.poison || a.width != b.width); }
inline Value cv_bvule(Value a, Value b) { return cv_bool(a.bits <= b.bits, a.poison || b.poison || a.width != b.width); }
inline Value cv_bvugt(Value a, Value b) { return cv_bool(a.bits > b.bits, a.poison || b.poison || a.width != b.width); }
inline Value cv_bvuge(Value a, Value b) { return cv_bool(a.bits >= b.bits, a.poison || b.poison || a.width != b.width); }

inline Value cv_ite(Value c, Value then_value, Value else_value) {
  bool take_true = (c.bits & 1U) != 0U;
  Value chosen = take_true ? then_value : else_value;
  return cv_value_w(chosen.bits, chosen.width, c.poison || chosen.poison);
}

struct ConstantInt {
  static Value get(uint32_t v) { return cv_value(v); }
};

struct IRBuilder {
  Value CreateAnd(Value a, Value b) {
    return cv_bvand(a, b);
  }
  Value CreateOr(Value a, Value b) {
    return cv_bvor(a, b);
  }
  Value CreateAdd(Value a, Value b) {
    return cv_bvadd(a, b);
  }
  Value CreateSub(Value a, Value b) {
    return cv_bvsub(a, b);
  }
  Value CreateURem(Value a, Value b) {
    return cv_bvurem(a, b);
  }
  Value CreateNSWAdd(Value a, Value b) {
    return cv_bvadd(a, b, true, false);
  }
  Value CreateSelect(Value c, Value x, Value y) {
    return cv_ite(c, x, y);
  }
  Value CreateOrPoisoning(Value a, Value b) {
    return cv_value_w((a.bits | b.bits) & 1U, 1, a.poison || b.poison);
  }
  Value CreateFreeze(Value a) {
    return cv_freeze(a);
  }
};

inline bool isKnownToBeAPowerOfTwo(Value v) {
  bool c = nondet_bool();
  if (c)
    CV_ASSUME(!v.poison && cv_is_power_of_two(v.bits));
  return c;
}

inline bool willNotOverflowSignedAdd(Value a, Value b) {
  bool c = nondet_bool();
  if (c)
    CV_ASSUME(!a.poison && !b.poison && !cv_sadd_overflow(a.bits, b.bits, a.width));
  return c;
}

#endif
