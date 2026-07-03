#pragma once

#include <cstddef>

#if !(defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
#include <cstdlib>
#include <iostream>
#endif

#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
#include <klee/klee.h>
// klee.h's klee_assert macro expands to __assert_fail -- a KLEE "special" function
// intercepted at runtime but not prototyped on every platform (e.g. macOS). Declare
// it so the symbolic harness compiles; KLEE supplies the real implementation.
extern "C" void __assert_fail(const char *, const char *, unsigned int,
                              const char *) __attribute__((noreturn));
#else
inline void klee_make_symbolic(void *, std::size_t, const char *) {}
inline void klee_assume(unsigned long long condition) {
  if (!condition) {
    std::cerr << "klee_assume failed in native execution\n";
    std::abort();
  }
}
inline void klee_assert(unsigned long long condition) {
  if (!condition) {
    std::cerr << "klee_assert failed in native execution\n";
    std::abort();
  }
}
#endif

namespace cv {

inline void cover(const char *name, bool reached) {
  if (reached) {
#if (defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
    klee_print_expr(name, 1);
#else
    (void)name;
#endif
  }
}

} // namespace cv
