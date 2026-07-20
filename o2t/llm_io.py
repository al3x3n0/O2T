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


def call_json_command(request: dict, command: str, timeout: int = 60) -> dict | None:
    """Run `command` with the JSON `request` on stdin and parse a JSON object from its stdout.

    The reply is extracted from the first `{` to the last `}` so providers that wrap JSON in prose
    or logs still parse. Returns the parsed dict, or None on ANY failure (spawn error, timeout,
    no/malformed JSON) -- advisory, never fatal."""
    try:
        proc = subprocess.run(command, shell=True, input=json.dumps(request),
                              capture_output=True, text=True, timeout=timeout)
        out = proc.stdout.strip()
        reply = json.loads(out[out.index("{"):out.rindex("}") + 1]) if "{" in out else None
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None
    return reply if isinstance(reply, dict) else None
