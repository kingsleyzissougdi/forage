"""Typed domain models for Forage experiments."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class FlowPhase(str, Enum):
    OPPORTUNITY = "OPPORTUNITY"
    CHEAP_TEST = "CHEAP_TEST"
    EXTERNAL_EVIDENCE = "EXTERNAL_EVIDENCE"
    FULFILLMENT = "FULFILLMENT"
    VERIFIED_ECONOMICS = "VERIFIED_ECONOMICS"
    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    STOP = "STOP"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    NEEDS_CHANNEL_SETUP = "NEEDS_CHANNEL_SETUP"
    NEEDS_BOOTSTRAP = "NEEDS_BOOTSTRAP"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class ExperimentStatus(str, Enum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    NEEDS_CHANNEL_SETUP = "NEEDS_CHANNEL_SETUP"
    NEEDS_BOOTSTRAP = "NEEDS_BOOTSTRAP"
    PAUSED = "PAUSED"
    CONTINUED = "CONTINUED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ActionStatus(str, Enum):
    RESERVED = "RESERVED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    DRY_RUN = "DRY_RUN"


class EvidenceKind(str, Enum):
    DEMAND = "DEMAND"
    BUYER = "BUYER"
    PRICE = "PRICE"
    RESPONSE = "RESPONSE"
    QUALITY = "QUALITY"
    OTHER = "OTHER"


class SetupClass(str, Enum):
    """Third-party setup friction (A best → E worst)."""

    A_NONE = "A"
    B_EXISTING = "B"
    C_AGENT_CREATABLE = "C"
    D_ONE_TIME_OWNER = "D"
    E_ONGOING_OWNER = "E"


class MechanismRole(str, Enum):
    RESEARCH_INFRASTRUCTURE = "RESEARCH_INFRASTRUCTURE"
    EARNING_MECHANISM = "EARNING_MECHANISM"


class MechanismSpec(BaseModel):
    """Minimal Gen-0 monetization-mechanism comparison record."""

    mechanism_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    mechanism_type: str
    role: MechanismRole = MechanismRole.EARNING_MECHANISM
    description: str
    payer: str
    paid_event: str
    evidence_urls: list[str] = Field(default_factory=list)
    evidence_notes: str = ""
    required_agent_action: str
    acceptance_method: str
    time_to_feedback_hours: float
    startup_cost_usd: Decimal
    expected_txn_value_usd: Decimal
    repeatability: float = Field(ge=0, le=1)  # 0–1 component
    automation_feasibility: float = Field(ge=0, le=1)
    ongoing_human_minutes_per_txn: float = 0
    one_time_bootstrap_minutes: float = 0
    setup_class: SetupClass = SetupClass.D_ONE_TIME_OWNER
    third_party_dependency: str = ""
    subjective_quality_risk: float = Field(ge=0, le=1)
    liability_reputation_risk: float = Field(ge=0, le=1)
    platform_account_risk: float = Field(ge=0, le=1)
    fulfillment_complexity: float = Field(ge=0, le=1)
    cheapest_validation: str
    blockers: list[str] = Field(default_factory=list)
    # Scoring components (filled by rubric; inspectable)
    score_components: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    eligible: bool = True
    reject_reasons: list[str] = Field(default_factory=list)

    @field_validator("startup_cost_usd", "expected_txn_value_usd", mode="before")
    @classmethod
    def _dec(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class PaidOpportunity(BaseModel):
    """Concrete currently-open paid opportunity (cash-path gate unit)."""

    opportunity_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    platform: str
    source_url: str
    title: str
    payout_usd_low: Decimal
    payout_usd_high: Decimal
    acceptance_rule: str
    required_deliverable: str
    currently_open: bool
    evidence_snippet: str = ""
    agent_exec_cost_usd: Decimal = Decimal("0")
    agent_exec_hours: float = 1.0
    ongoing_human_minutes: float = 0
    setup_class: SetupClass = SetupClass.D_ONE_TIME_OWNER
    setup_required: str = ""
    autonomy_confidence: float = Field(ge=0, le=1, default=0.5)
    key_risk: str = ""
    mechanism_type: str = ""
    reject_reasons: list[str] = Field(default_factory=list)
    eligible: bool = True
    # 0–5 component scores
    scores: dict[str, float] = Field(default_factory=dict)
    rank_score: float = 0.0

    @field_validator(
        "payout_usd_low",
        "payout_usd_high",
        "agent_exec_cost_usd",
        mode="before",
    )
    @classmethod
    def _dec(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class AgentEconomicRole(str, Enum):
    """Economic game the organism plays (not a CrewAI role)."""

    WORKER = "WORKER"
    MERCHANT = "MERCHANT"
    REFINERY = "REFINERY"
    BROKER = "BROKER"
    OPERATOR = "OPERATOR"


class LoopArrow(BaseModel):
    """One evidenced step in an economic loop."""

    description: str
    evidence_urls: list[str] = Field(default_factory=list)
    evidence_notes: str = ""


class EconomicLoopSpec(BaseModel):
    """Complete produce→sell→payment loop with evidence on every arrow."""

    loop_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    name: str
    scout: str
    agent_role: AgentEconomicRole
    summary: str
    input_arrow: LoopArrow
    value_creation: LoopArrow
    buyer_discovery: LoopArrow
    transaction: LoopArrow
    fulfillment: LoopArrow
    verified_payment: LoopArrow
    marginal_cost: LoopArrow
    sell_price_usd: Decimal
    marginal_cost_usd: Decimal
    contribution_margin_usd: Decimal = Decimal("0")
    ongoing_human_minutes: float = 0
    one_time_bootstrap_minutes: float = 0
    setup_class: SetupClass = SetupClass.D_ONE_TIME_OWNER
    setup_required: str = ""
    afternoon_experiment: str = ""
    afternoon_budget_usd: Decimal = Decimal("20")
    automation_pct: float = Field(ge=0, le=1, default=0.8)
    repeatability: float = Field(ge=0, le=1, default=0.7)
    time_to_first_payment_hours: float = 24
    key_risk: str = ""
    reject_reasons: list[str] = Field(default_factory=list)
    eligible: bool = True
    scores: dict[str, float] = Field(default_factory=dict)
    rank_score: float = 0.0

    @field_validator(
        "sell_price_usd",
        "marginal_cost_usd",
        "contribution_margin_usd",
        "afternoon_budget_usd",
        mode="before",
    )
    @classmethod
    def _dec(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class ExperimentSpec(BaseModel):
    """Minimal V1 experiment specification."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 1
    parent_id: str | None = None
    customer: str
    problem: str
    offer: str
    price_usd: Decimal
    acquisition_channel: str
    fulfillment_method: str
    permitted_actions: list[str]
    budget_usd: Decimal
    deadline: datetime
    evidence_target: str
    continue_condition: str
    pause_condition: str
    stop_condition: str
    mutation_field: str | None = None

    @field_validator("price_usd", "budget_usd", mode="before")
    @classmethod
    def _decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    def clone_with(self, field: str, value: Any) -> ExperimentSpec:
        """Clone while changing exactly one declared field."""
        data = self.model_dump()
        if field not in data:
            raise ValueError(f"unknown ExperimentSpec field: {field}")
        if field in {"id", "version", "parent_id", "mutation_field"}:
            raise ValueError(f"cannot mutate identity field via clone_with: {field}")
        data[field] = value
        data["parent_id"] = self.id
        data["version"] = self.version + 1
        data["id"] = str(uuid4())
        data["mutation_field"] = field
        return ExperimentSpec.model_validate(data)


class CampaignState(BaseModel):
    """CrewAI Flow structured state for one campaign run."""

    campaign_id: str = Field(default_factory=lambda: str(uuid4()))
    phase: FlowPhase = FlowPhase.OPPORTUNITY
    active_experiment_ids: list[str] = Field(default_factory=list)
    last_decision: str = ""
    notes: str = ""
    setup_request: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
