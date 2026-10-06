"""SQLite authoritative ledger for experiments, actions, evidence, economics."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from forage.models import (
    ActionStatus,
    EvidenceKind,
    ExperimentSpec,
    ExperimentStatus,
    FlowPhase,
)
from forage.policy import PolicyConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    parent_id TEXT,
    mutation_field TEXT,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    budget_usd TEXT NOT NULL,
    deadline TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    external_id TEXT,
    cost_estimate_usd TEXT NOT NULL,
    actual_cost_usd TEXT,
    reserved_usd TEXT NOT NULL DEFAULT '0',
    detail_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(experiment_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    reference TEXT,
    externally_observed INTEGER NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS economics (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount_usd TEXT NOT NULL,
    fees_usd TEXT NOT NULL DEFAULT '0',
    is_demo INTEGER NOT NULL DEFAULT 0,
    external_ref TEXT,
    detail_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_ledger (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    global_spent_usd TEXT NOT NULL,
    global_reserved_usd TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(v: Decimal | str | float | int) -> Decimal:
    return Decimal(str(v))


class FabricatedRevenueError(ValueError):
    pass


class BudgetExceededError(ValueError):
    pass


class PolicyBlockedError(ValueError):
    pass


class Ledger:
    """Authoritative SQLite store. Agents never write economics directly."""

    def __init__(self, path: Path | str, policy: PolicyConfig):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT id FROM budget_ledger WHERE id = 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO budget_ledger (id, global_spent_usd, global_reserved_usd) VALUES (1, '0', '0')")
            conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- experiments ---------------------------------------------------------

    def upsert_experiment(
        self,
        spec: ExperimentSpec,
        status: ExperimentStatus = ExperimentStatus.DRAFT,
        phase: FlowPhase = FlowPhase.OPPORTUNITY,
    ) -> None:
        now = _now()
        with self._tx() as conn:
            existing = conn.execute("SELECT id FROM experiments WHERE id = ?", (spec.id,)).fetchone()
            payload = json.dumps(spec.model_dump(mode="json"))
            if existing:
                conn.execute(
                    """
                    UPDATE experiments SET version=?, parent_id=?, mutation_field=?,
                    status=?, phase=?, spec_json=?, budget_usd=?, deadline=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        spec.version,
                        spec.parent_id,
                        spec.mutation_field,
                        status.value,
                        phase.value,
                        payload,
                        str(spec.budget_usd),
                        spec.deadline.isoformat(),
                        now,
                        spec.id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO experiments (
                      id, version, parent_id, mutation_field, status, phase,
                      spec_json, budget_usd, deadline, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        spec.id,
                        spec.version,
                        spec.parent_id,
                        spec.mutation_field,
                        status.value,
                        phase.value,
                        payload,
                        str(spec.budget_usd),
                        spec.deadline.isoformat(),
                        now,
                        now,
                    ),
                )

    def set_experiment_state(self, experiment_id: str, *, status: ExperimentStatus | None = None, phase: FlowPhase | None = None) -> None:
        with self._tx() as conn:
            row = conn.execute("SELECT status, phase FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(experiment_id)
            conn.execute(
                "UPDATE experiments SET status=?, phase=?, updated_at=? WHERE id=?",
                (
                    (status or ExperimentStatus(row["status"])).value,
                    (phase or FlowPhase(row["phase"])).value,
                    _now(),
                    experiment_id,
                ),
            )

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(experiment_id)
            return dict(row)

    def list_experiments(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM experiments ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]

    def count_running(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM experiments WHERE status IN ('RUNNING','WAITING_EXTERNAL')").fetchone()
            return int(row["c"])

    # --- budget --------------------------------------------------------------

    def _experiment_committed(self, conn: sqlite3.Connection, experiment_id: str) -> Decimal:
        rows = conn.execute(
            """
            SELECT reserved_usd, actual_cost_usd, status FROM actions
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        ).fetchall()
        total = Decimal("0")
        for r in rows:
            if r["status"] in (ActionStatus.RESERVED.value, ActionStatus.UNKNOWN.value):
                total += _d(r["reserved_usd"])
            elif r["status"] in (
                ActionStatus.SUCCEEDED.value,
                ActionStatus.DRY_RUN.value,
                ActionStatus.FAILED.value,
            ):
                total += _d(r["actual_cost_usd"] or "0")
        return total

    def reserve_budget(
        self,
        *,
        experiment_id: str,
        action_type: str,
        actor: str,
        tool: str | None,
        estimate_usd: Decimal,
        idempotency_key: str,
        detail: dict[str, Any] | None = None,
    ) -> str:
        """Atomically reserve budget before a billable action. Returns action_id."""
        estimate = _d(estimate_usd)
        if estimate < 0:
            raise ValueError("estimate must be >= 0")
        if estimate > self.policy.max_transaction_usd:
            raise BudgetExceededError("exceeds max_transaction_usd")
        if not self.policy.allows_action(action_type):
            action_id = str(uuid4())
            now = _now()
            with self._tx() as conn:
                conn.execute(
                    """
                    INSERT INTO actions (
                      id, experiment_id, action_type, actor, tool, status,
                      idempotency_key, cost_estimate_usd, reserved_usd, detail_json,
                      created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        action_id,
                        experiment_id,
                        action_type,
                        actor,
                        tool,
                        ActionStatus.BLOCKED.value,
                        idempotency_key,
                        str(estimate),
                        "0",
                        json.dumps({"reason": "action_type_not_allowed", **(detail or {})}),
                        now,
                        now,
                    ),
                )
            raise PolicyBlockedError(f"action_type not allowed: {action_type}")

        with self._tx() as conn:
            existing = conn.execute(
                "SELECT id, status FROM actions WHERE experiment_id=? AND idempotency_key=?",
                (experiment_id, idempotency_key),
            ).fetchone()
            if existing:
                return str(existing["id"])

            exp = conn.execute("SELECT budget_usd FROM experiments WHERE id=?", (experiment_id,)).fetchone()
            if exp is None:
                raise KeyError(experiment_id)

            bud = conn.execute("SELECT global_spent_usd, global_reserved_usd FROM budget_ledger WHERE id=1").fetchone()
            global_spent = _d(bud["global_spent_usd"])
            global_reserved = _d(bud["global_reserved_usd"])
            exp_committed = self._experiment_committed(conn, experiment_id)
            exp_budget = min(_d(exp["budget_usd"]), self.policy.per_experiment_spend_cap_usd)

            if exp_committed + estimate > exp_budget:
                raise BudgetExceededError("experiment budget exceeded")
            if global_spent + global_reserved + estimate > self.policy.global_spend_cap_usd:
                raise BudgetExceededError("global budget exceeded")

            action_id = str(uuid4())
            now = _now()
            status = ActionStatus.DRY_RUN.value if self.policy.dry_run and estimate > 0 else ActionStatus.RESERVED.value
            reserved = Decimal("0") if status == ActionStatus.DRY_RUN.value else estimate
            conn.execute(
                """
                INSERT INTO actions (
                  id, experiment_id, action_type, actor, tool, status,
                  idempotency_key, cost_estimate_usd, reserved_usd, detail_json,
                  created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    action_id,
                    experiment_id,
                    action_type,
                    actor,
                    tool,
                    status,
                    idempotency_key,
                    str(estimate),
                    str(reserved),
                    json.dumps(detail or {}),
                    now,
                    now,
                ),
            )
            if reserved > 0:
                conn.execute(
                    "UPDATE budget_ledger SET global_reserved_usd=? WHERE id=1",
                    (str(global_reserved + reserved),),
                )
            return action_id

    def reconcile_action(
        self,
        action_id: str,
        *,
        actual_cost_usd: Decimal,
        status: ActionStatus,
        external_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        actual = _d(actual_cost_usd)
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            reserved = _d(row["reserved_usd"])
            bud = conn.execute("SELECT global_spent_usd, global_reserved_usd FROM budget_ledger WHERE id=1").fetchone()
            global_spent = _d(bud["global_spent_usd"])
            global_reserved = _d(bud["global_reserved_usd"])
            new_reserved = max(Decimal("0"), global_reserved - reserved)
            new_spent = global_spent + actual
            if new_spent > self.policy.global_spend_cap_usd:
                raise BudgetExceededError("reconcile would exceed global cap")
            merged = json.loads(row["detail_json"] or "{}")
            if detail:
                merged.update(detail)
            conn.execute(
                """
                UPDATE actions SET status=?, actual_cost_usd=?, reserved_usd='0',
                external_id=COALESCE(?, external_id), detail_json=?, updated_at=?
                WHERE id=?
                """,
                (status.value, str(actual), external_id, json.dumps(merged), _now(), action_id),
            )
            conn.execute(
                "UPDATE budget_ledger SET global_spent_usd=?, global_reserved_usd=? WHERE id=1",
                (str(new_spent), str(new_reserved)),
            )

    def mark_unknown(self, action_id: str, detail: dict[str, Any] | None = None) -> None:
        """Timeout on external write → UNKNOWN; must reconcile before retry."""
        with self._tx() as conn:
            row = conn.execute("SELECT detail_json FROM actions WHERE id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            merged = json.loads(row["detail_json"] or "{}")
            if detail:
                merged.update(detail)
            merged["needs_reconcile"] = True
            conn.execute(
                "UPDATE actions SET status=?, detail_json=?, updated_at=? WHERE id=?",
                (ActionStatus.UNKNOWN.value, json.dumps(merged), _now(), action_id),
            )

    # --- evidence / economics ------------------------------------------------

    def add_evidence(
        self,
        *,
        experiment_id: str,
        kind: EvidenceKind,
        source: str,
        summary: str,
        externally_observed: bool,
        reference: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        eid = str(uuid4())
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO evidence (
                  id, experiment_id, kind, source, reference, externally_observed,
                  summary, payload_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    eid,
                    experiment_id,
                    kind.value,
                    source,
                    reference,
                    1 if externally_observed else 0,
                    summary,
                    json.dumps(payload or {}),
                    _now(),
                ),
            )
        return eid

    def record_economic_event(
        self,
        *,
        experiment_id: str,
        kind: str,
        amount_usd: Decimal,
        fees_usd: Decimal = Decimal("0"),
        is_demo: bool = False,
        external_ref: str | None = None,
        detail: dict[str, Any] | None = None,
        trusted: bool = False,
    ) -> str:
        """Only trusted adapters may create verified economic events."""
        if not trusted:
            raise FabricatedRevenueError("agents cannot set revenue/profit directly")
        if kind == "payment_received" and not is_demo and not external_ref:
            raise FabricatedRevenueError("live payment requires external_ref")
        eid = str(uuid4())
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO economics (
                  id, experiment_id, kind, amount_usd, fees_usd, is_demo,
                  external_ref, detail_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    eid,
                    experiment_id,
                    kind,
                    str(_d(amount_usd)),
                    str(_d(fees_usd)),
                    1 if is_demo else 0,
                    external_ref,
                    json.dumps(detail or {}),
                    _now(),
                ),
            )
        return eid

    def contribution_profit(self, experiment_id: str, *, include_demo: bool = False) -> Decimal:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, amount_usd, fees_usd, is_demo FROM economics WHERE experiment_id=?",
                (experiment_id,),
            ).fetchall()
        profit = Decimal("0")
        for r in rows:
            if r["is_demo"] and not include_demo:
                continue
            amt = _d(r["amount_usd"])
            fees = _d(r["fees_usd"])
            if r["kind"] in ("payment_received",):
                profit += amt - fees
            elif r["kind"] in ("refund", "cost", "fee"):
                profit -= amt + fees
        return profit

    def totals(self) -> dict[str, Decimal]:
        with self._connect() as conn:
            bud = conn.execute("SELECT global_spent_usd, global_reserved_usd FROM budget_ledger WHERE id=1").fetchone()
            rev = conn.execute("SELECT COALESCE(SUM(CAST(amount_usd AS REAL)),0) AS s FROM economics WHERE kind='payment_received' AND is_demo=0").fetchone()
        return {
            "spent": _d(bud["global_spent_usd"]),
            "reserved": _d(bud["global_reserved_usd"]),
            "verified_revenue": Decimal(str(rev["s"])),
        }

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO campaign_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM campaign_meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else default
