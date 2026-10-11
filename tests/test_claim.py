"""The claim: exactly one approval wins, even under contention, on a real database."""

from __future__ import annotations

import threading
from datetime import timedelta

from agent_approval_patterns import proposals
from agent_approval_patterns.db import connect
from conftest import add_order, add_proposal

STALE = timedelta(minutes=15)


def _claim(conn, pid, run, user="alice", action="retry_release"):
    return proposals.claim(conn, pid, user=user, approval_run_id=run,
                           selected_action=action, stale_after=STALE)


def test_first_claim_wins_second_loses(conn):
    pid = add_proposal(conn, add_order(conn))
    assert _claim(conn, pid, "run-1", user="alice")
    assert not _claim(conn, pid, "run-2", user="bob")
    row = proposals.get(conn, pid)
    assert (row["status"], row["approved_by"], row["approval_run_id"]) == \
        ("APPROVED", "alice", "run-1")


def test_concurrent_claims_exactly_one_winner(conn, database_url):
    pid = add_proposal(conn, add_order(conn))
    n = 20
    barrier = threading.Barrier(n)
    wins: list[str] = []
    lock = threading.Lock()

    def approver(i: int) -> None:
        with connect(database_url) as own_conn:  # one connection per approver
            barrier.wait()
            if _claim(own_conn, pid, f"run-{i}", user=f"user-{i}"):
                with lock:
                    wins.append(f"run-{i}")

    threads = [threading.Thread(target=approver, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(wins) == 1
    assert proposals.get(conn, pid)["approval_run_id"] == wins[0]


def test_stale_claim_can_be_taken_over(conn):
    pid = add_proposal(conn, add_order(conn))
    assert _claim(conn, pid, "run-1")
    conn.execute("UPDATE proposals SET status_updated_at = now() - interval '16 minutes' "
                 "WHERE id = %s", (pid,))
    assert _claim(conn, pid, "run-2", user="bob")
    assert proposals.get(conn, pid)["approval_run_id"] == "run-2"


def test_fresh_claim_cannot_be_taken_over(conn):
    pid = add_proposal(conn, add_order(conn))
    assert _claim(conn, pid, "run-1")
    conn.execute("UPDATE proposals SET status_updated_at = now() - interval '14 minutes' "
                 "WHERE id = %s", (pid,))
    assert not _claim(conn, pid, "run-2")


def test_only_claim_holder_can_close(conn):
    pid = add_proposal(conn, add_order(conn))
    assert _claim(conn, pid, "run-1")
    assert not proposals.close(conn, pid, approval_run_id="run-2", final_status="COMPLETED",
                               execution_result={}, verification_result=None)
    assert proposals.close(conn, pid, approval_run_id="run-1", final_status="COMPLETED",
                           execution_result={}, verification_result=None)


def test_action_not_in_proposal_cannot_be_claimed(conn):
    pid = add_proposal(conn, add_order(conn), actions=["place_on_hold"])
    assert not _claim(conn, pid, "run-1", action="retry_release")
    assert proposals.get(conn, pid)["status"] == "PENDING"


def test_release_returns_proposal_to_pending(conn):
    pid = add_proposal(conn, add_order(conn))
    assert _claim(conn, pid, "run-1")
    assert not proposals.release(conn, pid, approval_run_id="run-other")
    assert proposals.release(conn, pid, approval_run_id="run-1")
    assert proposals.get(conn, pid)["status"] == "PENDING"


def test_rejected_proposal_cannot_be_approved(conn):
    pid = add_proposal(conn, add_order(conn))
    assert proposals.reject(conn, pid, user="alice", reason="not needed")
    assert not _claim(conn, pid, "run-1")
