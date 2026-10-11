"""The proposal store: the durable record of every escalation.

Every state change is one conditional UPDATE. The WHERE clause carries the
precondition, so the database decides who wins and the row count says whether
you did.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

PENDING = "PENDING"
APPROVED = "APPROVED"  # claimed; an approval run is (or was) working on it
REJECTED = "REJECTED"
# Terminal statuses written by close():
COMPLETED = "COMPLETED"  # executed and verified
FAILED = "FAILED"  # executed and verification showed it did not work, or the call failed
UNCONFIRMED = "UNCONFIRMED"  # executed, but we could not confirm the effect
AUDITED = "AUDITED"  # audit mode: recorded what would have run, called nothing
NOT_EXECUTED = "NOT_EXECUTED"  # the action has no implementation; nothing ran


def create(
    conn: psycopg.Connection,
    *,
    agent: str,
    order_id: str,
    reason: str,
    proposed_actions: list[str],
    source_run_id: str,
    context: dict[str, Any] | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO proposals (agent, order_id, reason, proposed_actions, context, source_run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (agent, order_id, reason, Jsonb(proposed_actions), Jsonb(context or {}), source_run_id),
    ).fetchone()
    return row["id"]


def get(conn: psycopg.Connection, proposal_id: int) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM proposals WHERE id = %s", (proposal_id,)).fetchone()


def list_by_status(conn: psycopg.Connection, status: str) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM proposals WHERE status = %s ORDER BY id", (status,)
    ).fetchall()


def claim(
    conn: psycopg.Connection,
    proposal_id: int,
    *,
    user: str,
    approval_run_id: str,
    selected_action: str,
    stale_after: timedelta,
) -> bool:
    """Try to become the one approval for this proposal.

    Wins if the proposal is PENDING, or if an earlier claim has gone stale
    (its approval run presumably died). Exactly one concurrent caller wins.
    """
    cur = conn.execute(
        """
        UPDATE proposals
        SET status = 'APPROVED',
            approved_by = %(user)s,
            approval_run_id = %(run)s,
            selected_action = %(action)s,
            status_updated_at = now()
        WHERE id = %(id)s
          AND proposed_actions ? %(action)s
          AND (status = 'PENDING'
               OR (status = 'APPROVED' AND status_updated_at < now() - %(stale)s))
        """,
        {"id": proposal_id, "user": user, "run": approval_run_id,
         "action": selected_action, "stale": stale_after},
    )
    return cur.rowcount == 1


def release(conn: psycopg.Connection, proposal_id: int, *, approval_run_id: str) -> bool:
    """Undo a claim whose approval run never started (e.g. the task could not be queued)."""
    cur = conn.execute(
        """
        UPDATE proposals
        SET status = 'PENDING', approved_by = NULL, approval_run_id = NULL,
            selected_action = NULL, status_updated_at = now()
        WHERE id = %s AND status = 'APPROVED' AND approval_run_id = %s
        """,
        (proposal_id, approval_run_id),
    )
    return cur.rowcount == 1


def close(
    conn: psycopg.Connection,
    proposal_id: int,
    *,
    approval_run_id: str,
    final_status: str,
    execution_result: dict[str, Any],
    verification_result: dict[str, Any] | None,
) -> bool:
    """Write the outcome. Only the run that still holds the claim can do this."""
    cur = conn.execute(
        """
        UPDATE proposals
        SET status = %s, execution_result = %s, verification_result = %s,
            status_updated_at = now()
        WHERE id = %s AND status = 'APPROVED' AND approval_run_id = %s
        """,
        (final_status, Jsonb(execution_result), Jsonb(verification_result),
         proposal_id, approval_run_id),
    )
    return cur.rowcount == 1


def reject(conn: psycopg.Connection, proposal_id: int, *, user: str, reason: str) -> bool:
    cur = conn.execute(
        """
        UPDATE proposals
        SET status = 'REJECTED', approved_by = %s,
            execution_result = %s, status_updated_at = now()
        WHERE id = %s AND status = 'PENDING'
        """,
        (user, Jsonb({"rejected_reason": reason}), proposal_id),
    )
    return cur.rowcount == 1
