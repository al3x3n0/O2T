#!/usr/bin/env python3
"""Budgeted LLM client for the verification agent (provider-agnostic, advisory-only transport)."""

from __future__ import annotations

from o2t.llm_io import call_json_command


class LLMClient:
    """Wrap the provider-agnostic `--llm-command` with a hard global call budget.

    `call` returns the parsed JSON reply, or None when the transport fails OR the budget is
    exhausted -- the loop treats both as "the LLM is unavailable" and winds down cleanly. The
    budget is global across every pass in a batch run: the agent spends it where the residue is."""

    def __init__(self, command: str, timeout: int = 60, budget: int = 25):
        self.command = command
        self.timeout = timeout
        self.budget = budget
        self.used = 0
        # THE FULL EXCHANGE, kept for debugging. The evidence log records the PARSED action, so a
        # malformed reply survives only as `invalid-action` and the text that would explain it is
        # gone -- which is the failure a live model actually produces. Each entry carries the
        # request sent, the raw stdout/stderr, the exit status, the elapsed time and why a reply
        # was rejected. Advisory data only: nothing here is read back by the loop.
        self.transcript: list = []

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    def call(self, request: dict) -> dict | None:
        if self.remaining <= 0:
            self.transcript.append({"seq": len(self.transcript) + 1, "skipped": "budget-exhausted"})
            return None
        self.used += 1
        record: dict = {"seq": len(self.transcript) + 1}
        reply = call_json_command(request, self.command, timeout=self.timeout, record=record)
        self.transcript.append(record)
        return reply
