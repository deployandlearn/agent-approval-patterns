"""Tests run against a real Postgres (docker compose up -d db), never a fake."""

from __future__ import annotations

import os

import psycopg
import pytest

from agent_approval_patterns import proposals
from agent_approval_patterns.approval_graph import Deps
from agent_approval_patterns.config import DEFAULT_DATABASE_URL, Settings
from agent_approval_patterns.db import connect, create_schema, reset
from agent_approval_patterns.release_service import ReleaseService

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        psycopg.connect(DATABASE_URL).close()
    except psycopg.OperationalError as exc:
        pytest.fail(f"Postgres not reachable at {DATABASE_URL}. "
                    f"Start it with `docker compose up -d db`. ({exc})")
    create_schema(DATABASE_URL)
    return DATABASE_URL


@pytest.fixture
def conn(database_url):
    reset(database_url)
    with connect(database_url) as c:
        yield c


@pytest.fixture
def settings(database_url) -> Settings:
    # Tests act for real unless they opt into audit mode.
    return Settings(database_url=database_url, audit_mode_default=False)


@pytest.fixture
def deps(settings) -> Deps:
    return Deps(settings=settings, service=ReleaseService(settings.database_url))


def add_order(conn, order_id="ORD-1", status="RELEASE_FAILED", attempts=0, blocked=False):
    conn.execute(
        "INSERT INTO orders (id, status, release_attempts, blocked) VALUES (%s, %s, %s, %s)",
        (order_id, status, attempts, blocked),
    )
    return order_id


def add_proposal(conn, order_id="ORD-1", actions=("retry_release", "place_on_hold")):
    return proposals.create(conn, agent="release_recovery", order_id=order_id,
                            reason="test", proposed_actions=list(actions),
                            source_run_id="agent-test")


def downstream_effects(conn, order_id="ORD-1") -> int:
    """How many release calls actually changed the order."""
    return conn.execute("SELECT count(*) AS n FROM release_calls WHERE order_id = %s AND applied",
                        (order_id,)).fetchone()["n"]
