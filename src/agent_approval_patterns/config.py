"""Settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/agent_approval_patterns"


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    # A claim older than this can be taken over (its approval run is presumed dead).
    claim_stale_after: timedelta = timedelta(minutes=15)
    # How long a worker owns a task before another worker may retry it.
    # Deliberately shorter than claim_stale_after so the two never coincide.
    task_lease: timedelta = timedelta(minutes=5)
    # Audit mode: True means agents record what they would do but never call downstream.
    audit_mode_default: bool = True
    audit_mode_overrides: dict[str, bool] = field(default_factory=dict)
    # Send the proposal id as an idempotency key with every downstream call.
    send_idempotency_key: bool = True
    # Escalate instead of retrying once an order has failed this many times.
    max_auto_retries: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        overrides = {
            key.removeprefix("AUDIT_MODE_").lower(): _bool(value)
            for key, value in os.environ.items()
            if key.startswith("AUDIT_MODE_")
        }
        return cls(
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            claim_stale_after=timedelta(minutes=int(os.environ.get("CLAIM_STALE_MINUTES", "15"))),
            task_lease=timedelta(minutes=int(os.environ.get("TASK_LEASE_MINUTES", "5"))),
            audit_mode_default=_bool(os.environ.get("AUDIT_MODE", "true")),
            audit_mode_overrides=overrides,
        )

    def __post_init__(self) -> None:
        if self.task_lease >= self.claim_stale_after:
            raise ValueError("task_lease must be shorter than claim_stale_after")
