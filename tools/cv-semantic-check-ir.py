#!/usr/bin/env python3
import argparse
import os
import pathlib
import shutil
import subprocess
import sys


SAMPLES = [
    (-3, -3),
    (-3, -1),
    (-1, 0),
    (0, 0),
    (0, 1),
    (1, 0),
    (1, 1),
    (2, 3),
    (3, 2),
    (7, 1),
]


def print_result(
    status,
    sample_count=0,
    mismatch_input="",
    before_output="",
    after_output="",
    message="",
):
    print(f"semantic_status={status}")
    print(f"sample_count={sample_count}")
    print(f"mismatch_input={mismatch_input}")
    print(f"before_output={before_output}")
    print(f"after_output={after_output}")
    print(f"message={message}")


def write_driver(path):
    rows = ",\n".join(f"    {{{a}, {b}}}" for a, b in SAMPLES)
    path.write_text(
        f"""#include <stdio.h>

extern int test(int a, int b);

int main(void) {{
  const int samples[][2] = {{
{rows}
  }};
  const int count = (int)(sizeof(samples) / sizeof(samples[0]));
  for (int index = 0; index < count; ++index) {{
    const int a = samples[index][0];
    const int b = samples[index][1];
    printf("%d,%d=%d\\n", a, b, test(a, b));
  }}
  return 0;
}}
""",
        encoding="utf-8",
    )


def run_command(command):
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def compile_binary(clang, ir_path, driver_path, output_path):
    return run_command([clang, str(ir_path), str(driver_path), "-o", str(output_path)])


def run_binary(path):
    return run_command([str(path)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--clang", default=os.environ.get("O2T_SEMANTIC_CLANG", os.environ.get("COMPILERVERIF_SEMANTIC_CLANG", "clang")))
    args = parser.parse_args()

    before = pathlib.Path(args.before)
    after = pathlib.Path(args.after)
    work_dir = pathlib.Path(args.work_dir)
    clang = args.clang

    if not before.is_file():
        print_result("error", message=f"missing before IR: {before}")
        return 1
    if not after.is_file():
        print_result("error", message=f"missing after IR: {after}")
        return 1
    if shutil.which(clang) is None and not pathlib.Path(clang).is_file():
        print_result("error", message=f"clang not found: {clang}")
        return 1

    work_dir.mkdir(parents=True, exist_ok=True)
    driver = work_dir / "driver.c"
    before_bin = work_dir / "before-bin"
    after_bin = work_dir / "after-bin"
    write_driver(driver)

    before_compile = compile_binary(clang, before, driver, before_bin)
    if before_compile.returncode != 0:
        print_result("error", message="failed to compile before IR: " + before_compile.stderr.strip())
        return 1

    after_compile = compile_binary(clang, after, driver, after_bin)
    if after_compile.returncode != 0:
        print_result("error", message="failed to compile after IR: " + after_compile.stderr.strip())
        return 1

    before_run = run_binary(before_bin)
    if before_run.returncode != 0:
        print_result("error", message="failed to run before binary: " + before_run.stderr.strip())
        return 1

    after_run = run_binary(after_bin)
    if after_run.returncode != 0:
        print_result("error", message="failed to run after binary: " + after_run.stderr.strip())
        return 1

    before_lines = before_run.stdout.splitlines()
    after_lines = after_run.stdout.splitlines()
    for index, (before_line, after_line) in enumerate(zip(before_lines, after_lines)):
        if before_line != after_line:
            a, b = SAMPLES[index]
            print_result(
                "mismatch",
                sample_count=len(SAMPLES),
                mismatch_input=f"{a},{b}",
                before_output=before_line,
                after_output=after_line,
                message="sample output mismatch",
            )
            return 2

    if before_lines != after_lines:
        print_result(
            "mismatch",
            sample_count=len(SAMPLES),
            before_output=";".join(before_lines),
            after_output=";".join(after_lines),
            message="different number of output lines",
        )
        return 2

    print_result("matched", sample_count=len(SAMPLES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
