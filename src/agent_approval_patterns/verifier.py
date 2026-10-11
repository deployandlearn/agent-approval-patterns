"""State-based verification: re-read the order and compare, don't trust the response."""

from __future__ import annotations

import time
from typing import Any

import psycopg

VERIFIED = "verified"  # the order reached the expected status
FAILED = "failed"  # the order moved somewhere else
UNCONFIRMED = "unconfirmed"  # nothing changed (yet); we can't say it worked
NOT_CHECKABLE = "not_checkable"  # no expected status, or the order is gone


def read_status(conn: psycopg.Connection, order_id: str) -> str | None:
    row = conn.execute("SELECT status FROM orders WHERE id = %s", (order_id,)).fetchone()
    return row["status"] if row else None


def classify(before: str | None, after: str | None, expected: str | None) -> str:
    if expected is None or after is None:
        return NOT_CHECKABLE
    if after == expected:
        return VERIFIED
    if after == before:
        return UNCONFIRMED
    return FAILED


def verify(
    conn: psycopg.Connection,
    *,
    order_id: str,
    before: str | None,
    expected: str | None,
    attempts: int = 3,
    delay_seconds: float = 0.2,
) -> dict[str, Any]:
    """Re-read until the outcome is settled or attempts run out.

    Only UNCONFIRMED is retried: the downstream system may apply the change
    asynchronously, so "unchanged" right after the call is not yet a failure.
    """
    outcome, after = NOT_CHECKABLE, None
    for attempt in range(1, attempts + 1):
        after = read_status(conn, order_id)
        outcome = classify(before, after, expected)
        if outcome != UNCONFIRMED:
            break
        if attempt < attempts:
            time.sleep(delay_seconds)
    return {"outcome": outcome, "before": before, "after": after,
            "expected": expected, "attempts": attempt}
