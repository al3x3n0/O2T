#!/usr/bin/env python3
"""Deterministic stand-in for an LLM `--llm-command`: JSON request on stdin -> JSON verdict.

Used by orchestrate_fixture to exercise the provider-agnostic LLM-brain hook without a real
model. It echoes a valid family from the request so the brain's parse/validate/annotate path
is covered. A real command would put any model behind the same stdin/stdout JSON contract.
"""

import json
import sys

req = json.load(sys.stdin)
families = [f["name"] for f in req.get("families", [])]
# Pick the deterministic primary if offered, else the first family -- always a VALID name.
choice = req.get("deterministic", {}).get("primary") or (families[0] if families else "cfg")
print(json.dumps({"family": choice, "confidence": 0.66,
                  "rationale": "stub: echoes a valid family for hook coverage"}))
