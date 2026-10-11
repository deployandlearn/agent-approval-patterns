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
    idempotency_key: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Run (or, in audit mode, record) one action. Always writes an action_log row.

    dry_run defaults to True: a caller that forgets to decide records, it doesn't act.
    """
    spec = ACTIONS.get(action)
    if spec is None:
        raise ValueError(f"unknown action {action!r}")

    request = {"operation": action, "order_id": order_id, "idempotency_key": idempotency_key}
    if spec.call is None:
        # Reported in audit mode too: a blocked action is a finding before go-live.
        result = {"mode": "not_implemented", "executed": False, "request": request}
    elif dry_run:
        # Record the exact request we would have sent, and send nothing.
        result = {"mode": "dry_run", "executed": False, "would_call": action, "request": request}
    else:
        try:
            response = spec.call(service, order_id, idempotency_key)
            result = {"mode": "executed", "executed": True, "response": response}
        except ReleaseError as exc:
            # The call was made; it failed. Still "executed" so we verify state.
            result = {"mode": "failed", "executed": True, "error": str(exc)}

    row = conn.execute(
        "INSERT INTO action_log (run_id, agent, order_id, action, mode, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (run_id, agent, order_id, action, result["mode"], Jsonb(result)),
    ).fetchone()
    return {"action": action, "log_id": row["id"], **result}


def record_outcome(conn: psycopg.Connection, log_id: int, outcome: str) -> None:
    """Attach the verification outcome to a real call. Dry runs never get one."""
    conn.execute(
        "UPDATE action_log SET outcome = %s WHERE id = %s AND mode IN ('executed', 'failed')",
        (outcome, log_id),
    )


def recently_dry_run(conn: psycopg.Connection, *, agent: str, order_id: str, action: str) -> bool:
    """True if this agent already recorded this dry run for this order in the last 24 hours.

    Scans repeat, and in audit mode nothing changes between them. Without this,
    one stuck order would count as a new would-be action every few minutes.
    """
    return conn.execute(
        "SELECT 1 FROM action_log WHERE agent = %s AND order_id = %s AND action = %s "
        "AND mode = 'dry_run' AND created_at > now() - interval '24 hours'",
        (agent, order_id, action),
    ).fetchone() is not None
