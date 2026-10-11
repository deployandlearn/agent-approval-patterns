"""The approval run: a fresh, short graph seeded from the proposal row.

load -> execute -> verify -> close   (verify is skipped when nothing executed)

Nothing here depends on the agent run that escalated still existing. The
proposal row has everything needed, so this works hours later, after deploys,
for any agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from agent_approval_patterns import proposals
from agent_approval_patterns.actions import ACTIONS, record_outcome, run_action
from agent_approval_patterns.audit_mode import is_audit_mode
from agent_approval_patterns.config import Settings
from agent_approval_patterns.db import connect
from agent_approval_patterns.release_service import ReleaseService
from agent_approval_patterns.verifier import FAILED, UNCONFIRMED, VERIFIED, read_status, verify


class ApprovalState(TypedDict, total=False):
    proposal_id: int
    approval_run_id: str
    agent: str
    order_id: str
    action: str
    before: str | None
    executed: bool
    execution: dict[str, Any]
    verification: dict[str, Any] | None
    final_status: str
    closed: bool
    skipped: str  # set when this run must not act (claim lost, or already closed)


@dataclass
class Deps:
    settings: Settings
    service: ReleaseService
    # Fault-injection hook, called after execute and before verify/close. Tests
    # use it to simulate a worker dying after the downstream call was sent.
    after_execute: Callable[[ApprovalState], None] = field(default=lambda state: None)


_FINAL_STATUS = {VERIFIED: proposals.COMPLETED, FAILED: proposals.FAILED,
                 UNCONFIRMED: proposals.UNCONFIRMED}


def build_approval_graph(deps: Deps):
    url = deps.settings.database_url

    def load(state: ApprovalState) -> ApprovalState:
        with connect(url) as conn:
            row = proposals.get(conn, state["proposal_id"])
            # A retried task can arrive after the run closed or lost its claim: do nothing.
            if row is None or row["approval_run_id"] != state["approval_run_id"]:
                return {"skipped": "claim not held by this run"}
            if row["status"] != proposals.APPROVED:
                return {"skipped": f"proposal already {row['status']}"}
            return {"agent": row["agent"], "order_id": row["order_id"],
                    "action": row["selected_action"],
                    "before": read_status(conn, row["order_id"])}

    def execute(state: ApprovalState) -> ApprovalState:
        key = (f"proposal-{state['proposal_id']}"
               if deps.settings.send_idempotency_key else None)
        with connect(url) as conn:
            result = run_action(
                conn, deps.service, action=state["action"], order_id=state["order_id"],
                agent=state["agent"], run_id=state["approval_run_id"],
                dry_run=is_audit_mode(deps.settings, state["agent"]), idempotency_key=key,
            )
        new_state: ApprovalState = {"execution": result, "executed": result["executed"]}
        deps.after_execute({**state, **new_state})
        return new_state

    def verify_node(state: ApprovalState) -> ApprovalState:
        with connect(url) as conn:
            result = verify(conn, order_id=state["order_id"], before=state["before"],
                            expected=ACTIONS[state["action"]].expected_status)
            record_outcome(conn, state["execution"]["log_id"], result["outcome"])
        return {"verification": result}

    def close(state: ApprovalState) -> ApprovalState:
        mode = state["execution"]["mode"]
        if mode == "dry_run":
            final = proposals.AUDITED
        elif mode == "not_implemented":
            final = proposals.NOT_EXECUTED
        elif mode == "failed":
            final = proposals.FAILED
        else:
            outcome = state["verification"]["outcome"]
            final = _FINAL_STATUS.get(outcome, proposals.UNCONFIRMED)
        with connect(url) as conn:
            closed = proposals.close(
                conn, state["proposal_id"], approval_run_id=state["approval_run_id"],
                final_status=final, execution_result=state["execution"],
                verification_result=state.get("verification"),
            )
        # closed is False when another run took over a stale claim: our result is not recorded.
        return {"final_status": final, "closed": closed}

    builder = StateGraph(ApprovalState)
    builder.add_node("load", load)
    builder.add_node("execute", execute)
    builder.add_node("verify", verify_node)
    builder.add_node("close", close)
    builder.set_entry_point("load")
    builder.add_conditional_edges(
        "load", lambda s: END if s.get("skipped") else "execute", ["execute", END]
    )
    builder.add_conditional_edges(
        "execute", lambda s: "verify" if s["executed"] else "close", ["verify", "close"]
    )
    builder.add_edge("verify", "close")
    builder.add_edge("close", END)
    return builder.compile()


def run_approval(deps: Deps, proposal_id: int, approval_run_id: str) -> ApprovalState:
    graph = build_approval_graph(deps)
    return graph.invoke({"proposal_id": proposal_id, "approval_run_id": approval_run_id})
