"""The action registry: what an agent or a reviewer can ask for, and how it runs.

Each action declares the order status it should produce, so the verifier can
check the effect instead of trusting the response. An action with no
implementation says so; it never reports success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from agent_approval_patterns.release_service import ReleaseError, ReleaseService


@dataclass(frozen=True)
class Action:
    name: str
    expected_status: str | None  # None: no observable state to verify against
    call: Callable[[ReleaseService, str, str | None], dict] | None  # None: not implemented


ACTIONS: dict[str, Action] = {
    "retry_release": Action("retry_release", "RELEASED", lambda s, o, k: s.release(o, k)),
    "place_on_hold": Action("place_on_hold", "ON_HOLD", lambda s, o, k: s.hold(o, k)),
    # Listed so agents can propose it; deliberately has no implementation yet.
    "reroute_to_other_site": Action("reroute_to_other_site", "RELEASED", None),
}


def run_action(
    conn: psycopg.Connection,
    service: ReleaseService,
    *,
    action: str,
    order_id: str,
    agent: str,
    run_id: str,
    dry_run: bool,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Run (or, in audit mode, record) one action. Always writes an action_log row."""
    spec = ACTIONS.get(action)
    if spec is None:
        raise ValueError(f"unknown action {action!r}")

    if dry_run:
        result = {"mode": "dry_run", "executed": False,
                  "would_call": action, "idempotency_key": idempotency_key}
    elif spec.call is None:
        result = {"mode": "not_implemented", "executed": False}
    else:
        try:
            response = spec.call(service, order_id, idempotency_key)
            result = {"mode": "executed", "executed": True, "response": response}
        except ReleaseError as exc:
            # The call was made; it failed. Still "executed" so we verify state.
            result = {"mode": "failed", "executed": True, "error": str(exc)}

    conn.execute(
        "INSERT INTO action_log (run_id, agent, order_id, action, mode, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (run_id, agent, order_id, action, result["mode"], Jsonb(result)),
    )
    return {"action": action, **result}
