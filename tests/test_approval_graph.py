"""The approval run: execute, verify, close, and what happens when it dies halfway."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from agent_approval_patterns import proposals
from agent_approval_patterns.approval_graph import Deps, run_approval
from agent_approval_patterns.release_service import Fault
from conftest import add_order, add_proposal, downstream_effects

STALE = timedelta(minutes=15)


def _approve(conn, pid, run, action="retry_release"):
    assert proposals.claim(conn, pid, user="alice", approval_run_id=run,
                           selected_action=action, stale_after=STALE)


def test_approved_release_is_executed_verified_and_closed(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    _approve(conn, pid, "run-1")

    result = run_approval(deps, pid, "run-1")

    row = proposals.get(conn, pid)
    assert result["closed"]
    assert row["status"] == proposals.COMPLETED
    assert row["verification_result"]["outcome"] == "verified"
    assert downstream_effects(conn) == 1


def test_rejected_call_closes_as_failed(conn, deps):
    pid = add_proposal(conn, add_order(conn, blocked=True))
    _approve(conn, pid, "run-1")

    run_approval(deps, pid, "run-1")

    row = proposals.get(conn, pid)
    assert row["status"] == proposals.FAILED
    assert row["execution_result"]["mode"] == "failed"


def test_ok_response_without_effect_is_unconfirmed_not_completed(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    _approve(conn, pid, "run-1")
    deps.service.fault = Fault.SILENT  # the service says OK and does nothing

    run_approval(deps, pid, "run-1")

    row = proposals.get(conn, pid)
    assert row["execution_result"]["response"] == {"ok": True}
    assert row["status"] == proposals.UNCONFIRMED


def test_unimplemented_action_is_reported_not_faked(conn, deps):
    pid = add_proposal(conn, add_order(conn), actions=["reroute_to_other_site"])
    _approve(conn, pid, "run-1", action="reroute_to_other_site")

    result = run_approval(deps, pid, "run-1")

    assert result.get("verification") is None  # verify was skipped
    assert proposals.get(conn, pid)["status"] == proposals.NOT_EXECUTED


def test_run_without_the_claim_does_nothing(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    _approve(conn, pid, "run-1")

    result = run_approval(deps, pid, "run-intruder")

    assert result["skipped"]
    assert downstream_effects(conn) == 0
    assert proposals.get(conn, pid)["status"] == proposals.APPROVED


def test_retried_run_after_close_does_nothing(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    _approve(conn, pid, "run-1")
    run_approval(deps, pid, "run-1")

    result = run_approval(deps, pid, "run-1")  # e.g. the queue delivered the task twice

    assert result["skipped"] == "proposal already COMPLETED"
    assert downstream_effects(conn) == 1


class WorkerDied(Exception):
    pass


def _crash(_state):
    raise WorkerDied("killed after the downstream call, before close")


@pytest.mark.parametrize("send_key, expected_effects", [(True, 1), (False, 2)])
def test_crash_after_call_then_stale_takeover(conn, deps, send_key, expected_effects):
    """The claim guarantees one approval, not one call. The idempotency key closes the gap."""
    pid = add_proposal(conn, add_order(conn))
    _approve(conn, pid, "run-1")
    settings = replace(deps.settings, send_idempotency_key=send_key)
    crashing = Deps(settings=settings, service=deps.service, after_execute=_crash)

    with pytest.raises(WorkerDied):
        run_approval(crashing, pid, "run-1")
    assert downstream_effects(conn) == 1
    assert proposals.get(conn, pid)["status"] == proposals.APPROVED  # stuck until stale

    conn.execute("UPDATE proposals SET status_updated_at = now() - interval '16 minutes'")
    _approve(conn, pid, "run-2")
    run_approval(Deps(settings=settings, service=deps.service), pid, "run-2")

    assert downstream_effects(conn) == expected_effects
    assert proposals.get(conn, pid)["approval_run_id"] == "run-2"


def test_audit_mode_records_and_calls_nothing(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    _approve(conn, pid, "run-1")
    audit = Deps(settings=replace(deps.settings, audit_mode_default=True), service=deps.service)

    run_approval(audit, pid, "run-1")

    row = proposals.get(conn, pid)
    assert row["status"] == proposals.AUDITED
    assert row["execution_result"]["would_call"] == "retry_release"
    assert conn.execute("SELECT count(*) AS n FROM release_calls").fetchone()["n"] == 0
