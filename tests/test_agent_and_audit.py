"""The agent: code decides, audit mode gates every action, escalation writes a row and ends."""

from __future__ import annotations

from dataclasses import replace

from agent_approval_patterns import proposals
from agent_approval_patterns.agent import AGENT, run_agent
from agent_approval_patterns.approval_graph import Deps
from agent_approval_patterns.audit_mode import is_audit_mode
from agent_approval_patterns.config import Settings
from conftest import add_order, downstream_effects


def test_audit_mode_defaults_to_on():
    assert is_audit_mode(Settings(), "anything")


def test_per_agent_override_beats_global():
    s = Settings(audit_mode_default=True, audit_mode_overrides={AGENT: False})
    assert not is_audit_mode(s, AGENT)
    assert is_audit_mode(s, "other_agent")


def test_agent_retries_under_limit_and_escalates_over_it(conn, deps):
    add_order(conn, "ORD-1", attempts=0)
    add_order(conn, "ORD-2", attempts=5)

    result = run_agent(deps)

    assert [a["order_id"] for a in result["acted"]] == ["ORD-1"]
    assert result["acted"][0]["verification"]["outcome"] == "verified"
    assert downstream_effects(conn, "ORD-1") == 1
    pending = proposals.list_by_status(conn, proposals.PENDING)
    assert [p["order_id"] for p in pending] == ["ORD-2"]
    assert downstream_effects(conn, "ORD-2") == 0


def test_agent_in_audit_mode_logs_but_calls_nothing(conn, deps):
    add_order(conn, "ORD-1", attempts=0)
    audit = Deps(settings=replace(deps.settings, audit_mode_default=True), service=deps.service)

    result = run_agent(audit)

    assert result["acted"][0]["mode"] == "dry_run"
    assert conn.execute("SELECT count(*) AS n FROM release_calls").fetchone()["n"] == 0
    log = conn.execute("SELECT mode FROM action_log").fetchall()
    assert [r["mode"] for r in log] == ["dry_run"]


def test_open_proposal_is_not_escalated_again(conn, deps):
    add_order(conn, "ORD-2", attempts=5)
    run_agent(deps)
    second = run_agent(deps)

    assert second["escalated"] == []
    assert len(proposals.list_by_status(conn, proposals.PENDING)) == 1
