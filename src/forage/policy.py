"""Owner-controlled policy. Enforced in code, not prompts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class PolicyConfig(BaseModel):
    global_spend_cap_usd: Decimal = Decimal("50.00")
    per_experiment_spend_cap_usd: Decimal = Decimal("20.00")
    max_transaction_usd: Decimal = Decimal("10.00")
    allowed_action_types: list[str] = Field(
        default_factory=lambda: [
            "public_research",
            "web_fetch",
            "browser_read",
            "prepare_artifact",
            "post_offer",
            "send_outreach",
            "collect_payment",
            "fulfill",
        ]
    )
    allowed_providers: list[str] = Field(default_factory=lambda: ["public_web", "mock", "manual_channel"])
    max_model_calls: int = 40
    max_tool_calls: int = 80
    max_concurrent_experiments: int = 3
    max_retries: int = 2
    deadline_hours: int = 72
    # Default: DRY RUN for spending and commercial writes
    dry_run: bool = True
    external_writes_enabled: bool = False
    browser_use_enabled: bool = False

    @field_validator(
        "global_spend_cap_usd",
        "per_experiment_spend_cap_usd",
        "max_transaction_usd",
        mode="before",
    )
    @classmethod
    def _dec(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    def allows_action(self, action_type: str) -> bool:
        return action_type in self.allowed_action_types

    def allows_provider(self, provider: str) -> bool:
        return provider in self.allowed_providers

    def allows_external_write(self, action_type: str) -> bool:
        write_types = {"post_offer", "send_outreach", "collect_payment", "fulfill"}
        if action_type not in write_types:
            return True
        if self.dry_run or not self.external_writes_enabled:
            return False
        return True


def load_policy(path: Path | None = None) -> PolicyConfig:
    path = path or Path("policy.yaml")
    if not path.exists():
        return PolicyConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return PolicyConfig.model_validate(data)
