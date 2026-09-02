#!/usr/bin/env python3
"""Provider-agnostic LLM transport shared by the orchestrator brain and the verification agent.

O2T's convention (`--llm-command`) is an arbitrary shell command that receives a JSON request on
stdin and returns a JSON reply on stdout -- NO provider is baked in; any model behind any CLI works
(e.g. `claude -p --output-format json`, a local server wrapper, or a deterministic test stub).
Failure is never fatal to the caller: any transport/parse error yields None, and the caller's
deterministic behaviour stands. This module owns only the TRANSPORT; each caller validates the
reply's content against its own schema (family whitelist, action registry, ...).
"""

from __future__ import annotations

import json
import subprocess
import time


def call_json_command(request: dict, command: str, timeout: int = 60,
                      record: dict | None = None) -> dict | None:
    """Run `command` with the JSON `request` on stdin and parse a JSON object from its stdout.

    The reply is extracted from the first `{` to the last `}` so providers that wrap JSON in prose
    or logs still parse. Returns the parsed dict, or None on ANY failure (spawn error, timeout,
    no/malformed JSON) -- advisory, never fatal.

    `record`, if given, is FILLED with the raw exchange: stdout, stderr, exit status, elapsed
    seconds, and why a reply was rejected. The parsed return value is unchanged, so this cannot
    alter any caller's behaviour -- it exists because the failure a live model actually produces is
    a MALFORMED reply, and by the time the caller sees `None` the text that would explain it is
    gone. Debugging an agent without it means guessing at what the model said."""
    started = time.monotonic()
    raw_out = raw_err = ""
    status = None
    reply = None
    why = None
    try:
        proc = subprocess.run(command, shell=True, input=json.dumps(request),
                              capture_output=True, text=True, timeout=timeout)
        raw_out, raw_err, status = proc.stdout, proc.stderr, proc.returncode
        out = raw_out.strip()
        reply = json.loads(out[out.index("{"):out.rindex("}") + 1]) if "{" in out else None
        if reply is None:
            why = "no JSON object in stdout"
    except subprocess.TimeoutExpired as exc:
        why = f"timeout after {timeout}s"
        raw_out, raw_err = (exc.stdout or b"").decode(errors="replace") if isinstance(
            exc.stdout, bytes) else (exc.stdout or ""), ""
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        why = f"{type(exc).__name__}: {str(exc)[:200]}"
    if not isinstance(reply, dict):
        reply, why = None, why or "reply was not a JSON object"
    if record is not None:
        record.update({"request": request, "stdout": raw_out[:20000], "stderr": raw_err[:4000],
                       "exit_status": status, "elapsed_s": round(time.monotonic() - started, 3),
                       "parsed": reply, "rejected_because": why})
    return reply
