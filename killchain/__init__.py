"""killchain: split a build so a frontier agent never sees the risky half.

The prompt is partitioned by *refusal risk*, not confidentiality. Anything a
commercial coding agent would refuse, hedge, simplify, or guess at is routed to
an abliterated orchestrator model, which then implements it locally. A frontier
agent (Claude Code, Codex) builds the rest against stubs.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
