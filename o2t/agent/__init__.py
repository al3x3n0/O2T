"""Compiler-verification agent: an LLM-driven batch-triage loop over the O2T toolchain.

The agent NEVER decides soundness. It routes: the LLM observes evidence, picks whitelisted
actions whose handlers run REAL verifiers (Z3/opt/clang decide), proposes artifacts the formal
core validates, and stages new-tool candidates in quarantine for human review. Everything the
agent derives is recorded under `pass["agent"]` with explicit trust labels; the deterministic
orchestrator headline is never rewritten. See docs/agent.md.
"""
