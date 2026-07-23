#!/usr/bin/env python3
"""The `o2t` front-door CLI (o2t/cli.py): a hermetic check of the dispatcher, needing NO toolchain.

The single `o2t` command is the newcomer's entry point over the ~160 cv-* shims. This gates its
contract without z3/opt/clang present, so it runs in any CI:

  * `version` prints the package version;
  * `list` enumerates the cv-* tools and honors a substring filter;
  * `run <unknown>` fails cleanly (exit 1) rather than tracebacking;
  * `doctor` returns EXIT 1 when the REQUIRED solver (z3) is absent and EXIT 0 when present -- the
    contract CI and setup scripts rely on -- and always reports a Result line.
Toolchain-dependent behavior (real doctor output) is covered by the tools that use z3/opt.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from o2t import cli  # noqa: E402


def _run(argv, env=None):
    """Invoke cli.main(argv) with stdout+stderr captured; returns (exit_code, text)."""
    old = dict(os.environ)
    if env:
        os.environ.update(env)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli.main(argv)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return code, buf.getvalue()


def main() -> int:
    # 1. version
    code, out = _run(["version"])
    assert code == 0 and out.startswith("o2t "), ("version", code, out)

    # 2. list -- there are many cv-* tools; the filter narrows and every hit is a real script.
    code, out = _run(["list"])
    assert code == 0 and "run <name>" in out, ("list", code, out)
    n_all = sum(1 for ln in out.splitlines() if ln.startswith("  ") and ln.strip())
    assert n_all > 50, ("expected many cv-* tools", n_all)
    code, out = _run(["list", "tv"])
    assert code == 0 and "compose-tv" in out and "tv-corpus" in out, ("filtered list", out)

    # 3. run of a nonexistent tool fails cleanly (no traceback), exit 1.
    code, out = _run(["run", "this-tool-does-not-exist"])
    assert code == 1 and "tool not found" in out, ("unknown run", code, out)

    # 4. doctor CONTRACT: exit 1 iff the required solver is missing, always a Result line.
    #    Force z3 absent by pointing every resolver env at a nonexistent path AND clearing PATH so
    #    shutil.which finds nothing. (Homebrew fallback is only for opt/clang/lli, not z3.)
    hidden = {"O2T_Z3": "/nonexistent/z3", "Z3": "/nonexistent/z3", "PATH": ""}
    code, out = _run(["doctor"], env=hidden)
    assert code == 1 and "NOT READY" in out and "z3" in out, ("doctor missing-z3 must exit 1", code, out)
    assert "Result:" in out, ("doctor always reports a Result", out)

    # 5. no subcommand prints help (exit 0), not a crash.
    code, out = _run([])
    assert code == 0 and "doctor" in out and "verify" in out, ("bare invocation shows help", code, out)

    print("cli_fixture OK: the `o2t` front-door dispatcher works with NO toolchain -- version/list/"
          "filter, clean failure on an unknown tool, help on no args, and the doctor exit-code "
          "contract (1 when z3 is missing, a Result line always). One discoverable command over ~160 shims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
