#!/usr/bin/env python3
"""Cover source-derived intent model-check harness generation and CLI behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from o2t.symexec.modelcheck_intents import parse_widths  # noqa: E402


FAKE_ENGINE = """#!/usr/bin/env python3
import sys

fn = ""
for i, arg in enumerate(sys.argv):
    if arg == "--function" and i + 1 < len(sys.argv):
        fn = sys.argv[i + 1]

if "unsound" in fn:
    print("VERIFICATION FAILED")
    print("Counterexample:")
    print("  function=" + fn)
    sys.exit(10)

print("VERIFICATION SUCCESSFUL")
sys.exit(0)
"""


def var(name: str) -> dict:
    return {"op": "var", "name": name}


def c(value: int, bits: int = 32) -> dict:
    return {"op": "bvconst", "bits": bits, "value": value}


def formal(before: dict, after: dict, variables: list[str] | None = None,
           bits: int | None = None) -> dict:
    out = {
        "domain": "scalar-bv32",
        "equivalence": "result",
        "variables": variables or ["x"],
        "poison_variables": [],
        "refinement": "refinement",
        "before": before,
        "after": after,
    }
    if bits is not None:
        out["variable_bits"] = {name: bits for name in out["variables"]}
    return out


def cfg_formal() -> dict:
    cond = var("cond")
    zero = c(0)
    return {
        "domain": "cfg-bv32",
        "equivalence": "reachable-result",
        "variables": ["cond", "then_value", "else_value"],
        "poison_variables": [],
        "refinement": "equality",
        "before": {
            "op": "ite",
            "args": [
                {"op": "ne", "args": [cond, zero]},
                var("then_value"),
                var("else_value"),
            ],
        },
        "after": {
            "op": "ite",
            "args": [
                {"op": "ne", "args": [cond, zero]},
                var("then_value"),
                var("else_value"),
            ],
        },
    }


def records() -> list[dict]:
    return [
        {
            "marker": "probe.synthetic.add-zero",
            "file": "sound.cpp",
            "line": 10,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(0)]}, var("x")),
            },
        },
        {
            "marker": "probe.synthetic.add-zero-i1",
            "file": "sound-i1.cpp",
            "line": 11,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(0, 1)]}, var("x"), bits=1),
            },
        },
        {
            "marker": "probe.synthetic.add-zero-i8",
            "file": "sound-i8.cpp",
            "line": 12,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(0, 8)]}, var("x"), bits=8),
            },
        },
        {
            "marker": "probe.synthetic.add-zero-i16",
            "file": "sound-i16.cpp",
            "line": 13,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(0, 16)]}, var("x"), bits=16),
            },
        },
        {
            "marker": "probe.synthetic.add-zero-i64",
            "file": "sound-i64.cpp",
            "line": 14,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(0, 64)]}, var("x"), bits=64),
            },
        },
        {
            "marker": "probe.synthetic.unsound-add-one",
            "file": "bad.cpp",
            "line": 20,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal(var("x"), {"op": "bvadd", "args": [var("x"), c(1)]}),
            },
        },
        {
            "marker": "probe.synthetic.unsupported-vector",
            "file": "vec.cpp",
            "line": 30,
            "proof_status": "unsupported",
            "intent_candidate": {
                "formal": {
                    "domain": "vector-bv32x4",
                    "equivalence": "vector-result",
                    "variables": ["v"],
                    "before": var("v"),
                    "after": var("v"),
                },
            },
        },
        {
            "marker": "probe.synthetic.cfg-diamond",
            "file": "cfg.cpp",
            "line": 40,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": cfg_formal(),
            },
        },
    ]


def expansion_records() -> list[dict]:
    return [
        {
            "marker": "probe.synthetic.width-portable",
            "file": "portable.cpp",
            "line": 40,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(0)]}, var("x")),
            },
        },
        {
            "marker": "probe.synthetic.nonportable-big-const",
            "file": "nonportable.cpp",
            "line": 50,
            "proof_status": "proved",
            "intent_candidate": {
                "formal": formal({"op": "bvadd", "args": [var("x"), c(65536)]}, var("x")),
            },
        },
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    assert parse_widths("8,8,16") == [8, 16]
    assert parse_widths("native") is None
    assert parse_widths(" native ") is None

    old_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        fake = td / "cbmc"
        fake.write_text(FAKE_ENGINE, encoding="utf-8")
        fake.chmod(0o755)
        os.environ["PATH"] = str(td) + os.pathsep + old_path
        try:
            input_path = td / "intent-validated.jsonl"
            out_dir = td / "modelcheck"
            summary = td / "summary.json"
            write_jsonl(input_path, records())

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-intents.py"),
                    "--input",
                    str(input_path),
                    "--out-dir",
                    str(out_dir),
                    "--engine",
                    "cbmc",
                    "--summary-json",
                    str(summary),
                ],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 1, proc.stdout + proc.stderr
            data = json.loads(summary.read_text(encoding="utf-8"))
            assert data["records"] == 8 and data["generated"] == 7, data
            assert data["proved"] == 6 and data["refuted"] == 1 and data["unsupported"] == 1, data
            assert data["widths"]["1"]["proved"] == 1, data["widths"]
            assert data["widths"]["8"]["proved"] == 1, data["widths"]
            assert data["widths"]["16"]["proved"] == 1, data["widths"]
            assert data["widths"]["32"]["proved"] == 2 and data["widths"]["32"]["refuted"] == 1, data["widths"]
            assert data["widths"]["64"]["proved"] == 1, data["widths"]
            native_domains = {
                result["marker"]: result.get("domain")
                for result in data["results"]
                if result.get("marker", "").startswith("probe.synthetic.add-zero-i")
            }
            assert native_domains["probe.synthetic.add-zero-i1"] == "scalar-bv1", native_domains
            assert native_domains["probe.synthetic.add-zero-i8"] == "scalar-bv8", native_domains
            assert native_domains["probe.synthetic.add-zero-i16"] == "scalar-bv16", native_domains
            assert native_domains["probe.synthetic.add-zero-i64"] == "scalar-bv64", native_domains
            assert "probe.synthetic.add-zero-i8 @8b scalar-bv8" in proc.stderr, proc.stderr
            assert "probe.synthetic.cfg-diamond @32b cfg-bv32" in proc.stderr, proc.stderr
            refuted = next(r for r in data["results"] if r["status"] == "refuted")
            assert refuted.get("witness_excerpt") and "Counterexample" in refuted["witness_excerpt"], refuted
            assert data["findings"] == [
                {
                    "file": "bad.cpp",
                    "function": "check_0005_probe_synthetic_unsound_add_one",
                    "harness": str(out_dir / "harnesses" / "check_0005_probe_synthetic_unsound_add_one.cpp"),
                    "line": 20,
                    "marker": "probe.synthetic.unsound-add-one",
                    "reason": "counterexample",
                    "record_index": 5,
                    "status": "refuted",
                    "width": 32,
                    "witness_excerpt": refuted["witness_excerpt"],
                }
            ], data["findings"]

            invalid_width_summary = td / "summary-invalid-width.json"
            invalid_width_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-intents.py"),
                    "--input",
                    str(input_path),
                    "--out-dir",
                    str(td / "modelcheck-invalid-width"),
                    "--engine",
                    "cbmc",
                    "--widths",
                    "8,bad",
                    "--summary-json",
                    str(invalid_width_summary),
                ],
                capture_output=True,
                text=True,
            )
            assert invalid_width_proc.returncode == 1, invalid_width_proc.stdout + invalid_width_proc.stderr
            invalid_width = json.loads(invalid_width_summary.read_text(encoding="utf-8"))
            assert invalid_width["selected_widths"] == [], invalid_width
            assert invalid_width["generated"] == 0 and invalid_width["error"] == 1, invalid_width
            assert invalid_width["widths"]["none"]["error"] == 1, invalid_width["widths"]
            assert invalid_width["results"][0]["reason"] == "unsupported-width:bad", invalid_width

            harnesses = sorted((out_dir / "harnesses").glob("*.cpp"))
            assert len(harnesses) == 7, harnesses
            for harness in harnesses:
                subprocess.run(
                    ["clang++", "-std=c++17", "-I", str(ROOT / "o2t" / "symexec"),
                     "-fsyntax-only", str(harness)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            expanded_input = td / "intent-expanded.jsonl"
            expanded_out = td / "modelcheck-expanded"
            expanded_summary = td / "summary-expanded.json"
            write_jsonl(expanded_input, expansion_records())
            expanded_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "cv-modelcheck-intents.py"),
                    "--input",
                    str(expanded_input),
                    "--out-dir",
                    str(expanded_out),
                    "--engine",
                    "cbmc",
                    "--widths",
                    "8,16,32,64",
                    "--summary-json",
                    str(expanded_summary),
                ],
                capture_output=True,
                text=True,
            )
            assert expanded_proc.returncode == 0, expanded_proc.stdout + expanded_proc.stderr
            expanded = json.loads(expanded_summary.read_text(encoding="utf-8"))
            assert expanded["records"] == 2 and expanded["instances"] == 8, expanded
            assert expanded["generated"] == 6 and expanded["proved"] == 6 and expanded["unsupported"] == 2, expanded
            assert expanded["selected_widths"] == [8, 16, 32, 64], expanded
            assert expanded["widths"]["8"]["proved"] == 1 and expanded["widths"]["8"]["unsupported"] == 1, expanded["widths"]
            assert expanded["widths"]["16"]["proved"] == 1 and expanded["widths"]["16"]["unsupported"] == 1, expanded["widths"]
            assert expanded["widths"]["32"]["proved"] == 2, expanded["widths"]
            assert expanded["widths"]["64"]["proved"] == 2, expanded["widths"]
            assert {"scalar-bv8", "scalar-bv16", "scalar-bv32", "scalar-bv64"}.issubset(
                {result["domain"] for result in expanded["results"] if result.get("status") == "proved"}
            ), expanded["results"]
            unsupported_domains = {
                (result["width"], result.get("domain"))
                for result in expanded["results"]
                if result.get("status") == "unsupported"
            }
            assert unsupported_domains == {(8, "scalar-bv8"), (16, "scalar-bv16")}, expanded["results"]
            assert "probe.synthetic.width-portable @8b scalar-bv8" in expanded_proc.stderr, expanded_proc.stderr
            assert "probe.synthetic.nonportable-big-const @16b scalar-bv16" in expanded_proc.stderr, expanded_proc.stderr
            expanded_harnesses = sorted((expanded_out / "harnesses").glob("*.cpp"))
            assert len(expanded_harnesses) == 6, expanded_harnesses
            assert any("_w8.cpp" in str(path) for path in expanded_harnesses), expanded_harnesses
            assert any("_w64.cpp" in str(path) for path in expanded_harnesses), expanded_harnesses
        finally:
            os.environ["PATH"] = old_path

    print("modelcheck_intents_fixture OK: generated scalar harnesses, fake-engine verdicts, "
          "unsupported records, and syntax checks are covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
