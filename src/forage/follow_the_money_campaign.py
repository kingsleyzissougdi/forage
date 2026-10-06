"""FOLLOW_THE_MONEY_V1 campaign runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forage.flow import default_db_path
from forage.follow_the_money import run_follow_the_money
from forage.ledger import Ledger
from forage.policy import load_policy


def run_follow_the_money_campaign(db_path: Path | None = None) -> dict[str, Any]:
    db = db_path or default_db_path()
    policy = load_policy(Path("policy.yaml"))
    ledger = Ledger(db, policy)

    report = run_follow_the_money()
    ledger.set_meta("gen0_follow_the_money", json.dumps(report, default=str)[:120000])
    ledger.set_meta("gen0_worker_listing_hunt", "CLOSED")
    # Never leave a Shopify ask from prior campaigns
    bootstrap = report.get("bootstrap_request") or ""
    if bootstrap.startswith("ONE bootstrap") and "shopify" not in bootstrap.lower():
        ledger.set_meta("setup_request", bootstrap)
        ledger.set_meta("bootstrap_request", bootstrap)
    else:
        ledger.set_meta("setup_request", "")
        ledger.set_meta("bootstrap_request", bootstrap)

    report["db_path"] = str(db)
    return report
