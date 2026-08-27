"""Business job handlers extracted from the giant local media worker.

The runner (`application/worker.py`) owns leases, progress heartbeats, FFmpeg
execution and output registration.  Each module here owns exactly one job
family's business flow through narrow injected ports, so business rules can
evolve without touching worker orchestration (design §13.2).
"""
