"""The HTTP flow: 202 then poll, 409 for the loser, 422 for an action not offered."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_approval_patterns import proposals
from agent_approval_patterns.api import create_app
from agent_approval_patterns.worker import run_once
from conftest import add_order, add_proposal


@pytest.fixture
def client(deps):
    return TestClient(create_app(deps))


def test_approve_returns_202_and_worker_completes_it(conn, deps, client):
    pid = add_proposal(conn, add_order(conn))

    resp = client.post(f"/proposals/{pid}/approve", json={"user": "alice", "action": "retry_release"})
    assert resp.status_code == 202
    status_url = resp.json()["status_url"]
    assert client.get(status_url).json()["status"] == "APPROVED"  # not done yet

    assert run_once(deps)
    assert client.get(status_url).json()["status"] == "COMPLETED"


def test_second_approver_gets_409_naming_the_first(conn, client):
    pid = add_proposal(conn, add_order(conn))
    client.post(f"/proposals/{pid}/approve", json={"user": "alice", "action": "retry_release"})

    resp = client.post(f"/proposals/{pid}/approve", json={"user": "bob", "action": "place_on_hold"})

    assert resp.status_code == 409
    assert resp.json()["decided_by"] == "alice"


def test_action_not_offered_is_422(conn, client):
    pid = add_proposal(conn, add_order(conn), actions=["place_on_hold"])

    resp = client.post(f"/proposals/{pid}/approve", json={"user": "alice", "action": "retry_release"})

    assert resp.status_code == 422
    assert proposals.get(conn, pid)["status"] == "PENDING"


def test_queue_failure_releases_the_claim(conn, deps):
    def broken_enqueue(*args, **kwargs):
        raise ConnectionError("queue down")

    client = TestClient(create_app(deps, enqueue=broken_enqueue))
    pid = add_proposal(conn, add_order(conn))

    resp = client.post(f"/proposals/{pid}/approve", json={"user": "alice", "action": "retry_release"})

    assert resp.status_code == 503
    assert proposals.get(conn, pid)["status"] == "PENDING"  # can be approved again


def test_reject_then_approve_is_409(conn, client):
    pid = add_proposal(conn, add_order(conn))
    assert client.post(f"/proposals/{pid}/reject", json={"user": "alice", "reason": "dup"}).status_code == 200

    resp = client.post(f"/proposals/{pid}/approve", json={"user": "bob", "action": "retry_release"})
    assert resp.status_code == 409


def test_worker_retries_a_task_after_a_crash(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    TestClient(create_app(deps)).post(
        f"/proposals/{pid}/approve", json={"user": "alice", "action": "retry_release"})

    def crash(_state):
        raise RuntimeError("worker died")

    crashing = type(deps)(settings=deps.settings, service=deps.service, after_execute=crash)
    assert run_once(crashing)  # first attempt dies after the call
    task = conn.execute("SELECT status, attempts FROM tasks").fetchone()
    assert (task["status"], task["attempts"]) == ("QUEUED", 1)

    assert run_once(deps)  # second attempt: same run id, idempotency key dedupes the call
    assert proposals.get(conn, pid)["status"] == "COMPLETED"
    assert conn.execute("SELECT count(*) AS n FROM release_calls WHERE applied").fetchone()["n"] == 1
