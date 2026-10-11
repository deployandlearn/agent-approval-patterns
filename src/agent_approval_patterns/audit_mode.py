"""Audit mode: an agent records what it would do, and calls nothing.

Resolution order: a per-agent override, then the global default. The default
is True, so a new agent, or a missing setting, can never act by accident.
"""

from __future__ import annotations

from agent_approval_patterns.config import Settings


def is_audit_mode(settings: Settings, agent: str) -> bool:
    return settings.audit_mode_overrides.get(agent.lower(), settings.audit_mode_default)
