"""A deterministic recovery agent for orders that failed to release.

find -> decide -> act -> escalate

Code decides, not a model: retry while the order is under the retry limit,
escalate to a person after that. When it escalates, it writes a proposal row
and finishes. Nothing waits in memory for the reviewer.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent_approval_patterns import proposals
from agent_approval_patterns.actions import ACTIONS, run_action
from agent_approval_patterns.approval_graph import Deps
from agent_approval_patterns.audit_mode import is_audit_mode
from agent_approval_patterns.db import connect
from agent_approval_patterns.verifier import read_status, verify

AGENT = "release_recovery"
ESCALATION_OPTIONS = ["retry_release", "place_on_hold", "reroute_to_other_site"]


class AgentState(TypedDict, total=False):
    run_id: str
    candidates: list[dict[str, Any]]
    to_retry: list[str]
    to_escalate: list[dict[str, Any]]
    acted: list[dict[str, Any]]
    escalated: list[int]


def build_agent_graph(deps: Deps):
    url = deps.settings.database_url

    def find(state: AgentState) -> AgentState:
        with connect(url) as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.release_attempts FROM orders o
                WHERE o.status = 'RELEASE_FAILED'
                  AND NOT EXISTS (SELECT 1 FROM proposals p WHERE p.order_id = o.id
                                  AND p.status IN ('PENDING', 'APPROVED'))
                ORDER BY o.id
                """
            ).fetchall()
        return {"candidates": rows}

    def decide(state: AgentState) -> AgentState:
        limit = deps.settings.max_auto_retries
        to_retry = [c["id"] for c in state["candidates"] if c["release_attempts"] < limit]
        to_escalate = [
            {"order_id": c["id"], "release_attempts": c["release_attempts"]}
            for c in state["candidates"] if c["release_attempts"] >= limit
        ]
        return {"to_retry": to_retry, "to_escalate": to_escalate}

    def act(state: AgentState) -> AgentState:
        dry_run = is_audit_mode(deps.settings, AGENT)
        acted = []
        with connect(url) as conn:
            for order_id in state["to_retry"]:
                before = read_status(conn, order_id)
                result = run_action(
                    conn, deps.service, action="retry_release", order_id=order_id,
                    agent=AGENT, run_id=state["run_id"], dry_run=dry_run,
                    idempotency_key=f"{state['run_id']}-{order_id}",
                )
                if result["executed"]:
                    result["verification"] = verify(
                        conn, order_id=order_id, before=before,
                        expected=ACTIONS["retry_release"].expected_status,
                    )
                acted.append({"order_id": order_id, **result})
        return {"acted": acted}

    def escalate(state: AgentState) -> AgentState:
        ids = []
        with connect(url) as conn:
            for item in state["to_escalate"]:
                ids.append(proposals.create(
                    conn, agent=AGENT, order_id=item["order_id"],
                    reason=f"release failed {item['release_attempts']} times",
                    proposed_actions=ESCALATION_OPTIONS,
                    source_run_id=state["run_id"], context=item,
                ))
        return {"escalated": ids}

    builder = StateGraph(AgentState)
    builder.add_node("find", find)
    builder.add_node("decide", decide)
    builder.add_node("act", act)
    builder.add_node("escalate", escalate)
    builder.set_entry_point("find")
    builder.add_edge("find", "decide")
    builder.add_edge("decide", "act")
    builder.add_edge("act", "escalate")
    builder.add_edge("escalate", END)
    return builder.compile()


def run_agent(deps: Deps) -> AgentState:
    return build_agent_graph(deps).invoke({"run_id": f"agent-{uuid.uuid4().hex[:12]}"})
