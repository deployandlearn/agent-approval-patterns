"""Runs queued approval tasks."""

from __future__ import annotations

import argparse
import logging
import time

from agent_approval_patterns import tasks
from agent_approval_patterns.approval_graph import Deps, run_approval
from agent_approval_patterns.config import Settings
from agent_approval_patterns.db import connect
from agent_approval_patterns.release_service import ReleaseService

log = logging.getLogger("agent_approval_patterns.worker")


def run_once(deps: Deps) -> bool:
    """Lease and run one task. Returns False when the queue is empty."""
    with connect(deps.settings.database_url) as conn:
        task = tasks.lease_next(conn, deps.settings.task_lease)
        if task is None:
            return False
        try:
            if task["kind"] != "approval":
                raise ValueError(f"unknown task kind {task['kind']!r}")
            result = run_approval(deps, task["payload"]["proposal_id"],
                                  task["payload"]["approval_run_id"])
            if result.get("skipped"):
                log.info("proposal %s: skipped (%s)", task["payload"]["proposal_id"],
                         result["skipped"])
            elif not result["closed"]:
                log.warning("proposal %s: claim was taken over; result not recorded",
                            task["payload"]["proposal_id"])
            tasks.complete(conn, task["id"])
        except Exception as exc:  # noqa: BLE001 - a worker records every failure
            log.exception("task %s failed", task["id"])
            tasks.fail(conn, task["id"], repr(exc))
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run queued approval tasks.")
    parser.add_argument("--drain", action="store_true", help="exit when the queue is empty")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    deps = Deps(settings=settings, service=ReleaseService(settings.database_url))
    log.info("worker started (audit mode default: %s)", settings.audit_mode_default)
    while True:
        if not run_once(deps):
            if args.drain:
                return
            time.sleep(1)


if __name__ == "__main__":
    main()
