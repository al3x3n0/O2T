"""Initial operator-supplied RV corpus and case-specific control requirements."""

EXTRA_MUTATIONS = {
    'upstream_simple': {'zero-sign': ' if (out[0] == 0.0f) out[0] = -out[0];'},
    'guarded_division': {'zero-divisor': ' for (int i=0;i<4;++i) if (y[i]==0u) out[i] ^= 1u;'},
}


def gap_requirements(name):
    controls = [{'id': control, 'artifact': name + '/negative-' + control + '.dylib'}
                for control in EXTRA_MUTATIONS.get(name, {})]
    predicates = []
    if name == 'guarded_division':
        predicates = [(1, 'zero'), (1, 'nonzero')]
    elif name == 'upstream_simple':
        predicates = [(array, p) for array in (0, 1) for p in ('positive_zero', 'negative_zero')]
    coverage = [{'id': 'input-' + str(array) + '-' + p.replace('_', '-'), 'array': array,
                 'predicate': p, 'lanes': 'each', 'minimum': 1} for array, p in predicates]
    return {'controls': controls, 'coverage': coverage}
CASES = [
    ("upstream_simple", "test_001_simple-wfv.cpp", "float", "T_TrT"),
    ("upstream_ifelse", "test_008_ifelse-wfv.cpp", "float_uniform", "U_TrT"),
    ("upstream_struct_calls", "test_006_scalarize-wfv.cpp", "float", "T_TrT"),
    ("upstream_loop_break", "test_018_loopmx02-wfv.cpp", "float", "T_TrT"),
    ("upstream_loop_varying", "test_019_loopmx03-wfv.cpp", "float", "T_TrT"),
    ("upstream_interleaved", "test_026_interleaved-wfv.cpp", "memory", "C_U"),
    ("upstream_masked_store", "test_037_maskedinterleaved-wfv.cpp", "memory", "C_U"),
    ("upstream_strided_store", "test_057_strided-wfv.cpp", "memory", "C_U"),
]
TARGETED = {
    "integer_branches": """extern "C" unsigned foo(unsigned x, unsigned y) {
  if (x & 1) return (x + y) ^ (y >> 3);
  if (y > 31) return (x >> 3) ^ y;
  return x - y;
}\n""",
    "guarded_division": """extern "C" unsigned foo(unsigned x, unsigned y) {
  if (y == 0) return x;
  return x / y;
}\n""",
    "integer_loop": """extern "C" unsigned foo(unsigned x, unsigned y) {
  unsigned acc = x;
  for (unsigned i=0; i<(y & 15); ++i) acc = (acc * 33u) ^ i;
  return acc;
}\n""",
    "integer_early_exit": """extern "C" unsigned foo(unsigned x, unsigned y) {
  unsigned acc = x;
  for (unsigned i=0; i<(y & 31); ++i) {
    acc = acc + (i ^ y);
    if ((acc & 7) == 0) break;
  }
  return acc;
}\n""",
}
