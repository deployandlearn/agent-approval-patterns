"""Create the schema and a handful of synthetic orders."""

from __future__ import annotations

from agent_approval_patterns.config import Settings
from agent_approval_patterns.db import connect, create_schema, reset

ORDERS = [
    # id, status, release_attempts, blocked
    ("ORD-1001", "RELEASE_FAILED", 0, False),  # under the retry limit: the agent retries it
    ("ORD-1002", "RELEASE_FAILED", 2, False),  # over the limit: escalated; approval fixes it
    ("ORD-1003", "RELEASE_FAILED", 3, True),   # escalated; the release service rejects it
    ("ORD-1004", "RELEASED", 1, False),        # healthy, ignored
]


def seed(database_url: str) -> None:
    create_schema(database_url)
    reset(database_url)
    with connect(database_url) as conn:
        for order in ORDERS:
            conn.execute(
                "INSERT INTO orders (id, status, release_attempts, blocked) VALUES (%s, %s, %s, %s)",
                order,
            )


def main() -> None:
    settings = Settings.from_env()
    seed(settings.database_url)
    print(f"seeded {len(ORDERS)} orders")


if __name__ == "__main__":
    main()
