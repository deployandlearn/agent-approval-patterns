"""Audit mode as evidence: fail closed, record the request, keep dry runs out of the stats."""

from __future__ import annotations

from dataclasses import replace

from agent_approval_patterns import proposals
from agent_approval_patterns.actions import run_action
from agent_approval_patterns.agent import run_agent
from agent_approval_patterns.approval_graph import Deps, run_approval
from agent_approval_patterns.audit_report import build_report
from conftest import add_order, add_proposal


def _audit(deps: Deps) -> Deps:
    return Deps(settings=replace(deps.settings, audit_mode_default=True), service=deps.service)


def test_caller_that_does_not_decide_gets_a_dry_run(conn, deps):
    add_order(conn)
    result = run_action(conn, deps.service, action="retry_release", order_id="ORD-1",
                        agent="new_agent", run_id="run-1")  # no dry_run argument

    assert result["mode"] == "dry_run"
    assert conn.execute("SELECT count(*) AS n FROM release_calls").fetchone()["n"] == 0


def test_dry_run_records_the_exact_request(conn, deps):
    add_order(conn)
    result = run_action(conn, deps.service, action="retry_release", order_id="ORD-1",
                        agent="a", run_id="run-1", idempotency_key="k-1")

    assert result["request"] == {"operation": "retry_release", "order_id": "ORD-1",
                                 "idempotency_key": "k-1"}


def test_unimplemented_action_is_reported_as_blocked_even_in_audit_mode(conn, deps):
    add_order(conn)
    result = run_action(conn, deps.service, action="reroute_to_other_site",
                        order_id="ORD-1", agent="a", run_id="run-1", dry_run=True)

    assert result["mode"] == "not_implemented"


def test_repeated_scans_record_one_dry_run_per_order_per_day(conn, deps):
    add_order(conn, "ORD-1", attempts=0)
    audit = _audit(deps)

    run_agent(audit)
    second = run_agent(audit)
    run_agent(audit)

    assert second["acted"][0]["mode"] == "dry_run_already_recorded"
    report = build_report(conn)
    assert [(r["runs"], r["orders"]) for r in report["would_be"]] == [(1, 1)]


def test_dry_runs_never_count_toward_success_rate(conn, deps):
    # Three dry runs that "would succeed", then one real call that fails.
    for i in range(3):
        add_order(conn, f"ORD-D{i}", attempts=0)
    run_agent(_audit(deps))
    add_order(conn, "ORD-REAL", attempts=0, blocked=True)
    conn.execute("UPDATE orders SET status = 'RELEASED' WHERE id LIKE 'ORD-D%'")
    run_agent(deps)

    report = build_report(conn)
    assert report["would_be"][0]["runs"] == 3
    [real] = report["real_outcomes"]
    assert (real["calls"], real["verified"], real["failed"]) == (1, 0, 1)
    assert real["success_rate"] == 0


def test_verified_real_call_counts_as_success(conn, deps):
    pid = add_proposal(conn, add_order(conn))
    assert proposals.claim(conn, pid, user="alice", approval_run_id="run-1",
                           selected_action="retry_release",
                           stale_after=deps.settings.claim_stale_after)
    run_approval(deps, pid, "run-1")

    [real] = build_report(conn)["real_outcomes"]
    assert (real["calls"], real["verified"], real["success_rate"]) == (1, 1, 1)


def test_agreement_is_measurable_in_audit_mode(conn, deps):
    stale = deps.settings.claim_stale_after
    for i, choice in enumerate(["retry_release", "retry_release", "place_on_hold"]):
        pid = add_proposal(conn, add_order(conn, f"ORD-{i}"))  # first choice: retry_release
        assert proposals.claim(conn, pid, user="alice", approval_run_id=f"run-{i}",
                               selected_action=choice, stale_after=stale)
        run_approval(_audit(deps), pid, f"run-{i}")

    [row] = build_report(conn)["agreement"]
    assert (row["decided"], row["picked_first_choice"]) == (3, 2)
    assert build_report(conn)["real_outcomes"] == []  # audit mode: nothing real to score
