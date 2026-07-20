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

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    def call(self, request: dict) -> dict | None:
        if self.remaining <= 0:
            return None
        self.used += 1
        return call_json_command(request, self.command, timeout=self.timeout)
