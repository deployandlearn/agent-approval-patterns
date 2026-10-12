"""Text a model might write can never become an action. Only registered action names run."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_approval_patterns import proposals
from agent_approval_patterns.actions import run_action
from agent_approval_patterns.api import create_app
from conftest import add_order, add_proposal

MODEL_TEXT = "Please retry the release for this order"


def test_free_text_is_not_an_action(conn, deps):
    add_order(conn)
    with pytest.raises(ValueError, match="unknown action"):
        run_action(conn, deps.service, action=MODEL_TEXT, order_id="ORD-1",
                   agent="a", run_id="run-1", dry_run=False)
    assert conn.execute("SELECT count(*) AS n FROM release_calls").fetchone()["n"] == 0


def test_free_text_cannot_be_approved(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    client = TestClient(create_app(deps))

    resp = client.post(f"/proposals/{pid}/approve", json={"user": "alice", "action": MODEL_TEXT})

    assert resp.status_code == 422
    assert proposals.get(conn, pid)["status"] == "PENDING"


def test_free_text_cannot_become_a_proposal_option(conn):
    add_order(conn)
    with pytest.raises(ValueError, match="unknown action"):
        add_proposal(conn, actions=["retry_release", MODEL_TEXT])
