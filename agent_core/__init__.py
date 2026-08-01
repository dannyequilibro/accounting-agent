"""Shared machinery for Danny's AI workers.

The accounting agent was the first worker. Everything in here is deliberately
free of invoice-specific logic so the next worker (recon, AR chasing, board
pack) reuses the same decision rights, approval loop, and audit trail instead
of reinventing them.

Three pieces:
  policy    — decides who owns an action: the agent, or Danny
  approvals — the loop that actually gets an answer back from Danny
  ledger    — the durable record of what was decided and why
"""
