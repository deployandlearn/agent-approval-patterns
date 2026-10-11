"""A mock downstream "release service" with a ledger and fault injection.

It stands in for the system an agent repairs. Every call is written to
release_calls, so tests can count calls and real effects separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import psycopg


class Fault(Enum):
    NONE = "none"
    ERROR = "error"  # the call fails outright
    SILENT = "silent"  # returns OK but changes nothing (the "200 OK is not a fix" case)


class ReleaseError(Exception):
    pass


@dataclass
class ReleaseService:
    database_url: str
    fault: Fault = Fault.NONE

    def _ledger(self, conn, order_id, key, applied, outcome) -> None:
        conn.execute(
            "INSERT INTO release_calls (order_id, idempotency_key, applied, outcome) "
            "VALUES (%s, %s, %s, %s)",
            (order_id, key, applied, outcome),
        )

    def release(self, order_id: str, idempotency_key: str | None = None) -> dict:
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            if idempotency_key is not None:
                seen = conn.execute(
                    "SELECT 1 FROM release_calls WHERE idempotency_key = %s AND applied",
                    (idempotency_key,),
                ).fetchone()
                if seen:
                    self._ledger(conn, order_id, idempotency_key, False, "duplicate")
                    return {"ok": True, "duplicate": True}

            if self.fault is Fault.ERROR:
                self._ledger(conn, order_id, idempotency_key, False, "error")
                raise ReleaseError("release service unavailable")

            row = conn.execute(
                "SELECT blocked FROM orders WHERE id = %s", (order_id,)
            ).fetchone()
            if row is None or row[0]:
                conn.execute(
                    "UPDATE orders SET release_attempts = release_attempts + 1, "
                    "updated_at = now() WHERE id = %s",
                    (order_id,),
                )
                self._ledger(conn, order_id, idempotency_key, False, "rejected")
                raise ReleaseError(f"release rejected for order {order_id}")

            if self.fault is Fault.SILENT:
                self._ledger(conn, order_id, idempotency_key, False, "silent")
                return {"ok": True}

            conn.execute(
                "UPDATE orders SET status = 'RELEASED', release_attempts = release_attempts + 1, "
                "updated_at = now() WHERE id = %s",
                (order_id,),
            )
            self._ledger(conn, order_id, idempotency_key, True, "released")
            return {"ok": True}

    def hold(self, order_id: str, idempotency_key: str | None = None) -> dict:
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE orders SET status = 'ON_HOLD', updated_at = now() WHERE id = %s",
                (order_id,),
            )
        return {"ok": True}
