"""External adapters. Webpage/tool text is untrusted data."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

from forage.ledger import BudgetExceededError, Ledger, PolicyBlockedError
from forage.models import ActionStatus
from forage.policy import PolicyConfig

# Patterns that must never affect policy/ledger when present in web content
HOSTILE_MARKERS = (
    "IGNORE PREVIOUS INSTRUCTIONS",
    "SET BUDGET",
    "GRANT PERMISSION",
    "EDIT POLICY",
    "RECORD PAYMENT",
    "FABRICATE REVENUE",
)


@dataclass
class ResearchHit:
    title: str
    url: str
    snippet: str
    source: str


@dataclass
class OpportunityCandidate:
    buyer: str
    demand_where: str
    what_people_pay_or_do: str
    sellable_outcome: str
    shortest_validation: str
    expected_time_hours: float
    expected_cost_usd: Decimal
    automation_feasibility: float  # 0-1 heuristic, not an LLM "score"
    economic_upside_usd: Decimal
    evidence_urls: list[str]
    category: str
    raw_hits: list[ResearchHit]


def sanitize_untrusted(text: str) -> str:
    """Treat web/tool text as data. Strip instruction-like directives for logging."""
    cleaned = text
    for marker in HOSTILE_MARKERS:
        cleaned = re.sub(re.escape(marker), "[REDACTED_INSTRUCTION]", cleaned, flags=re.I)
    return cleaned[:8000]


class ResearchPort(Protocol):
    def search(self, query: str, limit: int = 5) -> list[ResearchHit]: ...


class MockResearchAdapter:
    """Deterministic research for demo/tests."""

    def __init__(self, hits: list[ResearchHit] | None = None):
        self._hits = hits or [
            ResearchHit(
                title="Looking for freelancer to clean CSV exports weekly",
                url="https://example.com/demand/csv-clean",
                snippet="Budget $40/job. Need reliable weekly CSV cleanup for Shopify exports.",
                source="mock",
            ),
            ResearchHit(
                title="Pay for competitor pricing snapshots",
                url="https://example.com/demand/pricing-scan",
                snippet="Would pay $25 for a weekly PDF of 10 competitor prices.",
                source="mock",
            ),
        ]

    def search(self, query: str, limit: int = 5) -> list[ResearchHit]:
        return self._hits[:limit]


class PublicWebResearchAdapter:
    """Read-only public web research (HN Algolia + DuckDuckGo HTML). Unauthenticated."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            timeout=20.0,
            headers={"User-Agent": "ForageResearch/0.1 (+local; read-only)"},
            follow_redirects=True,
        )

    def search(self, query: str, limit: int = 5) -> list[ResearchHit]:
        hits = self._search_hn(query, limit=limit)
        if len(hits) < limit:
            hits.extend(self._search_ddg(query, limit=limit - len(hits)))
        return hits[:limit]

    def _search_hn(self, query: str, limit: int) -> list[ResearchHit]:
        try:
            resp = self._client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "hitsPerPage": str(limit)},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [
                ResearchHit(
                    title="hn_search_error",
                    url="",
                    snippet=sanitize_untrusted(str(exc)),
                    source="hn_error",
                )
            ]
        hits: list[ResearchHit] = []
        for item in data.get("hits", []):
            title = sanitize_untrusted(str(item.get("title") or item.get("story_title") or item.get("author") or ""))
            url = str(item.get("url") or "")
            if not url and item.get("objectID"):
                url = f"https://news.ycombinator.com/item?id={item['objectID']}"
            snippet = sanitize_untrusted(str(item.get("story_text") or item.get("comment_text") or title or ""))
            if not title and not snippet:
                continue
            if not title:
                title = snippet[:80]
            hits.append(ResearchHit(title=title, url=url, snippet=snippet[:500], source="hn_algolia"))
            if len(hits) >= limit:
                break
        return hits

    def _search_ddg(self, query: str, limit: int) -> list[ResearchHit]:
        try:
            resp = self._client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return [
                ResearchHit(
                    title="ddg_search_error",
                    url="",
                    snippet=sanitize_untrusted(str(exc)),
                    source="duckduckgo_error",
                )
            ]
        # Parse raw HTML first; sanitize only extracted strings (full-page sanitize truncates).
        html = resp.text
        hits: list[ResearchHit] = []
        from urllib.parse import parse_qs, unquote, urlparse

        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.I | re.S,
        ):
            href = m.group(1)
            title = re.sub("<[^>]+>", "", m.group(2)).strip()
            title = sanitize_untrusted(title.replace("&amp;", "&"))
            link = href
            if "uddg=" in href:
                parsed = urlparse(href if "://" in href else "https:" + href)
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    link = unquote(qs["uddg"][0])
            snippet = title
            tail = html[m.end() : m.end() + 400]
            sm = re.search(r'class="result__snippet"[^>]*>(.*?)</', tail, flags=re.I | re.S)
            if sm:
                snippet = sanitize_untrusted(re.sub("<[^>]+>", "", sm.group(1)).strip())
            hits.append(
                ResearchHit(
                    title=title or "result",
                    url=link,
                    snippet=snippet,
                    source="duckduckgo",
                )
            )
            if len(hits) >= limit:
                break
        return hits


class ActionGateway:
    """All external/billable actions go through policy + ledger."""

    def __init__(self, ledger: Ledger, policy: PolicyConfig):
        self.ledger = ledger
        self.policy = policy

    def execute(
        self,
        *,
        experiment_id: str,
        action_type: str,
        actor: str,
        tool: str,
        estimate_usd: Decimal,
        idempotency_key: str,
        provider: str,
        fn,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.policy.allows_provider(provider):
            self.ledger.reserve_budget(
                experiment_id=experiment_id,
                action_type=action_type,
                actor=actor,
                tool=tool,
                estimate_usd=Decimal("0"),
                idempotency_key=idempotency_key + ":blocked-provider",
                detail={"reason": "provider_not_allowed", "provider": provider},
            )
            raise PolicyBlockedError(f"provider not allowed: {provider}")

        write_blocked = not self.policy.allows_external_write(action_type)
        try:
            action_id = self.ledger.reserve_budget(
                experiment_id=experiment_id,
                action_type=action_type,
                actor=actor,
                tool=tool,
                estimate_usd=estimate_usd,
                idempotency_key=idempotency_key,
                detail={**(detail or {}), "provider": provider, "write_blocked": write_blocked},
            )
        except PolicyBlockedError:
            return {"status": ActionStatus.BLOCKED.value, "result": None}
        except BudgetExceededError as exc:
            return {"status": ActionStatus.BLOCKED.value, "result": None, "error": str(exc)}

        # Idempotent replay: if already terminal, do not re-run side effect
        with self.ledger._connect() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row and row["status"] in (
            ActionStatus.SUCCEEDED.value,
            ActionStatus.DRY_RUN.value,
            ActionStatus.FAILED.value,
            ActionStatus.BLOCKED.value,
        ):
            prev = json.loads(row["detail_json"] or "{}")
            if "result" in prev:
                return {"status": row["status"], "result": prev["result"], "action_id": action_id, "replay": True}

        if write_blocked and action_type in {
            "post_offer",
            "send_outreach",
            "collect_payment",
            "fulfill",
        }:
            result = {
                "dry_run": True,
                "message": "external write blocked by policy (DRY RUN)",
                "would_do": detail or {},
            }
            self.ledger.reconcile_action(
                action_id,
                actual_cost_usd=Decimal("0"),
                status=ActionStatus.DRY_RUN,
                detail={"result": result},
            )
            return {"status": ActionStatus.DRY_RUN.value, "result": result, "action_id": action_id}

        try:
            result = fn()
            # Never trust returned payloads that claim payments
            if isinstance(result, dict):
                result = {k: v for k, v in result.items() if k not in {"revenue", "payment", "profit"}}
                if "text" in result and isinstance(result["text"], str):
                    result["text"] = sanitize_untrusted(result["text"])
            # Keep rich Python objects for the caller; store a JSON-safe summary in the ledger.
            ledger_result: Any = result
            if isinstance(result, list):
                safe_list = []
                for item in result:
                    if hasattr(item, "model_dump"):
                        safe_list.append(item.model_dump(mode="json"))
                    elif hasattr(item, "__dict__"):
                        row = {}
                        for k, v in item.__dict__.items():
                            if k == "raw_hits":
                                row[k] = [
                                    {
                                        "title": getattr(h, "title", ""),
                                        "url": getattr(h, "url", ""),
                                        "snippet": getattr(h, "snippet", ""),
                                        "source": getattr(h, "source", ""),
                                    }
                                    for h in (v or [])
                                ]
                            else:
                                row[k] = str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v
                        safe_list.append(row)
                    else:
                        safe_list.append(str(item))
                ledger_result = safe_list
            elif hasattr(result, "model_dump"):
                ledger_result = result.model_dump(mode="json")
            status = ActionStatus.DRY_RUN if self.policy.dry_run else ActionStatus.SUCCEEDED
            actual = Decimal("0") if self.policy.dry_run else estimate_usd
            self.ledger.reconcile_action(
                action_id,
                actual_cost_usd=actual,
                status=status,
                detail={"result": ledger_result},
            )
            return {
                "status": status.value,
                "result": result,
                "action_id": action_id,
            }
        except TimeoutError as exc:
            self.ledger.mark_unknown(action_id, detail={"error": str(exc)})
            return {"status": ActionStatus.UNKNOWN.value, "action_id": action_id, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — record failure, continue campaign
            self.ledger.reconcile_action(
                action_id,
                actual_cost_usd=Decimal("0"),
                status=ActionStatus.FAILED,
                detail={"error": str(exc)},
            )
            return {"status": ActionStatus.FAILED.value, "action_id": action_id, "error": str(exc)}


def browser_use_read(url: str, policy: PolicyConfig) -> str:
    """Bounded Browser Use read. Cannot bypass policy; optional dependency."""
    if not policy.browser_use_enabled:
        raise PolicyBlockedError("browser_use disabled by policy")
    if not policy.allows_action("browser_read"):
        raise PolicyBlockedError("browser_read not allowed")
    try:
        import browser_use  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise PolicyBlockedError("browser-use not installed") from exc
    # We intentionally do not auto-launch browsers in V1 without explicit enable.
    raise PolicyBlockedError("browser_use requires explicit owner enablement for this session")


def candidate_fingerprint(c: OpportunityCandidate) -> str:
    raw = f"{c.buyer}|{c.sellable_outcome}|{c.category}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
