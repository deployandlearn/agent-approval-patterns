"""A minimal Postgres task queue.

A worker leases a task for settings.task_lease. If the worker dies, the lease
expires and another worker retries the task, up to max_attempts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def enqueue(conn: psycopg.Connection, kind: str, payload: dict[str, Any]) -> int:
    row = conn.execute(
        "INSERT INTO tasks (kind, payload) VALUES (%s, %s) RETURNING id",
        (kind, Jsonb(payload)),
    ).fetchone()
    return row["id"]


def lease_next(conn: psycopg.Connection, lease: timedelta) -> dict[str, Any] | None:
    return conn.execute(
        """
        UPDATE tasks SET status = 'RUNNING', attempts = attempts + 1,
                         locked_until = now() + %s
        WHERE id = (
            SELECT id FROM tasks
            WHERE attempts < max_attempts
              AND (status = 'QUEUED' OR (status = 'RUNNING' AND locked_until < now()))
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING *
        """,
        (lease,),
    ).fetchone()


def complete(conn: psycopg.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET status = 'DONE', locked_until = NULL WHERE id = %s", (task_id,))


def fail(conn: psycopg.Connection, task_id: int, error: str) -> None:
    # Back to QUEUED while attempts remain; FAILED once they are used up.
    conn.execute(
        """
        UPDATE tasks
        SET status = CASE WHEN attempts < max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
            locked_until = NULL, last_error = %s
        WHERE id = %s
        """,
        (error, task_id),
    )
