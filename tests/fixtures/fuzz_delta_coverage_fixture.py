#!/usr/bin/env python3
"""Lock in the before->after DELTA coverage signal of cv-fuzz-campaign.

The absolute optimized-output opcode histogram fingerprints what the output LOOKS LIKE; the delta
fingerprints what the optimizer DID. This test pins the plateau-breaking property: two runs whose
optimized OUTPUT is byte-for-byte identical (same absolute histogram) but that reached it via
different transformations get DISTINCT fingerprints once the delta is included. Also checks the
signed-bucket semantics (removed opcodes are negative, introduced ones positive)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_tool():
    spec = importlib.util.spec_from_file_location("cv_fuzz_campaign", ROOT / "tools" / "cv-fuzz-campaign.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    fz = _load_tool()

    # signed-bucket semantics: 0 -> 0, removal negative, introduction positive, magnitude bucketed.
    assert fz.signed_bucket(0) == 0
    assert fz.signed_bucket(-6) == -fz.bucket(6) < 0, "removed work must bucket negative"
    assert fz.signed_bucket(6) == fz.bucket(6) > 0, "introduced work must bucket positive"

    # Identical OUTPUT, different TRANSFORMATION -> absolute coverage collides, full fingerprint doesn't.
    after = "%1 = add i32 %a, %b\n%2 = add i32 %1, %c\nret i32 %2\n"
    before_noop = after                                              # optimizer changed nothing
    before_work = "%1 = add i32 %a, %b\n" + "%x = mul i32 %a, %a\n" * 6 + after  # removed 6 mul + 1 add

    abs_cov = fz.opcode_coverage(after)
    fp_noop = abs_cov | fz.delta_coverage(fz.opcode_histogram(before_noop), fz.opcode_histogram(after))
    fp_work = abs_cov | fz.delta_coverage(fz.opcode_histogram(before_work), fz.opcode_histogram(after))

    assert fz.opcode_coverage(after) == abs_cov, "sanity: same output -> same absolute histogram"
    assert fp_noop != fp_work, "delta must distinguish two transformations with identical output"

    # the 'work' run's delta records the removed muls/adds (negative), the 'noop' run's is all-zero.
    d_work = {k: v for k, v in fz.delta_coverage(fz.opcode_histogram(before_work), fz.opcode_histogram(after)) if v != 0}
    assert ("delta:mul", fz.signed_bucket(-6)) in fz.delta_coverage(
        fz.opcode_histogram(before_work), fz.opcode_histogram(after)), d_work
    assert ("delta:total", fz.signed_bucket(-6)) in {(k, v) for k, v in
        fz.delta_coverage(fz.opcode_histogram(before_work), fz.opcode_histogram(after))}
    assert all(v == 0 for _, v in fz.delta_coverage(
        fz.opcode_histogram(before_noop), fz.opcode_histogram(after))), "noop transform must be all-zero delta"

    print("fuzz_delta_coverage_fixture OK: before->after delta distinguishes two transformations "
          "with identical optimized output (absolute histogram collides); signed buckets record "
          "removed (negative) vs introduced (positive) opcodes -- the plateau-breaking signal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
