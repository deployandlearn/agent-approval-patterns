"""The audit-mode report: the evidence you look at before letting an agent act.

Three kinds of evidence, kept apart on purpose:

- would_be:      what agents would have done (dry runs). Shows volume and reach.
- real_outcomes: calls that were actually made, with their verified outcome.
                 Dry runs are excluded: "would run" is not "worked".
- agreement:     how often a reviewer picked the agent's first-choice fix.
                 This works during audit mode, because people still decide.
"""

from __future__ import annotations

from typing import Any

import psycopg


def build_report(conn: psycopg.Connection) -> dict[str, Any]:
    would_be = conn.execute(
        """
        SELECT agent, action, count(*) AS runs, count(DISTINCT order_id) AS orders
        FROM action_log WHERE mode = 'dry_run'
        GROUP BY agent, action ORDER BY agent, action
        """
    ).fetchall()

    blocked = conn.execute(
        """
        SELECT agent, action, count(*) AS runs
        FROM action_log WHERE mode = 'not_implemented'
        GROUP BY agent, action ORDER BY agent, action
        """
    ).fetchall()

    real_outcomes = conn.execute(
        """
        SELECT action,
               count(*) AS calls,
               count(*) FILTER (WHERE outcome = 'verified') AS verified,
               count(*) FILTER (WHERE outcome = 'failed' OR mode = 'failed') AS failed,
               count(*) FILTER (WHERE outcome = 'unconfirmed') AS unconfirmed,
               round(count(*) FILTER (WHERE outcome = 'verified')::numeric
                     / count(*), 2) AS success_rate
        FROM action_log WHERE mode IN ('executed', 'failed')
        GROUP BY action ORDER BY action
        """
    ).fetchall()

    agreement = conn.execute(
        """
        SELECT agent,
               count(*) AS decided,
               count(*) FILTER (WHERE selected_action = proposed_actions->>0) AS picked_first_choice
        FROM proposals WHERE selected_action IS NOT NULL
        GROUP BY agent ORDER BY agent
        """
    ).fetchall()

    return {"would_be": would_be, "blocked": blocked,
            "real_outcomes": real_outcomes, "agreement": agreement}
