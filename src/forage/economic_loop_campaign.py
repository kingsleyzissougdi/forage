"""ECONOMIC_LOOP_DISCOVERY_V1 campaign runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forage.economic_loops import discover_economic_loops, loops_report, select_top_loops
from forage.flow import default_db_path
from forage.ledger import Ledger
from forage.policy import load_policy


def run_economic_loop_discovery(db_path: Path | None = None) -> dict[str, Any]:
    db = db_path or default_db_path()
    policy = load_policy(Path("policy.yaml"))
    ledger = Ledger(db, policy)

    loops = discover_economic_loops()
    top = select_top_loops(loops, k=3)
    report = loops_report(loops, top)

    ledger.set_meta("gen0_worker_listing_hunt", "CLOSED")
    ledger.set_meta(
        "ZERO_SETUP_OBJECTIVE_TASK_MARKET",
        ledger.get_meta("ZERO_SETUP_OBJECTIVE_TASK_MARKET") or "SPARSE",
    )
    ledger.set_meta("gen0_economic_loops", json.dumps(report, default=str)[:100000])
    ledger.set_meta(
        "gen0_economic_loops_top3",
        json.dumps(report["top3"], default=str)[:50000],
    )
    bootstrap = report.get("bootstrap_request") or ""
    if bootstrap.startswith("ONE bootstrap"):
        ledger.set_meta("setup_request", bootstrap)
        ledger.set_meta("bootstrap_request", bootstrap)
    else:
        # Do not leave stale KYC asks from listing-hunt era
        ledger.set_meta("setup_request", "")
        ledger.set_meta("bootstrap_request", bootstrap)

    report["db_path"] = str(db)
    report["totals"] = {k: str(v) for k, v in ledger.totals().items()}
    return report
