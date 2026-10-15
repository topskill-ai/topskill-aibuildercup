"""A2A lane: AP2-style mandates, Agent Cards, the paid JSON-RPC endpoint, the directory and the
Enterprise Assistant demo caller (SPEC §8.6, §9). Public functions per SPEC §14.2."""

from topskill.a2a.executor import A2AStack, get_stack
from topskill.a2a.mandates import call_id_for, jwks, reserve_call, sign_mandate, verify_token

__all__ = ["A2AStack", "call_id_for", "get_stack", "jwks", "reserve_call", "sign_mandate", "verify_token"]
