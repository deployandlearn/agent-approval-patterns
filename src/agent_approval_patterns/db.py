"""Connections and schema setup."""

from __future__ import annotations

from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

TABLES = ("release_calls", "tasks", "action_log", "proposals", "orders")


def connect(database_url: str) -> psycopg.Connection:
    # Autocommit: every statement in this kit is a single atomic statement by design.
    return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)


def create_schema(database_url: str) -> None:
    sql = files("agent_approval_patterns").joinpath("schema.sql").read_text()
    with connect(database_url) as conn:
        conn.execute(sql)


def reset(database_url: str) -> None:
    with connect(database_url) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
