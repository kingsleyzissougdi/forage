"""ECONOMIC_LOOP_DISCOVERY_V1: merchant/refinery/broker loop scouts.

Worker listing hunts are closed (SPARSE). This module discovers evidenced
economic loops and ranks afternoon experiments — no live treasury spend.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

import httpx

from forage.adapters.gateway import sanitize_untrusted
from forage.models import (
    AgentEconomicRole,
    EconomicLoopSpec,
    LoopArrow,
    SetupClass,
)

UA = {"User-Agent": "ForageResearch/0.1 (+local; loop-discovery)"}

ARROW_NAMES = (
    "input_arrow",
    "value_creation",
    "buyer_discovery",
    "transaction",
    "fulfillment",
    "verified_payment",
    "marginal_cost",
)

# Known x402 facilitator discovery endpoints (read-only; surface not spine).
X402_DISCOVERY_URLS = [
    "https://x402.org/facilitator/discovery/resources",
    "https://facilitator.x402.org/discovery/resources",
    "https://www.x402.org/facilitator/discovery/resources",
]


def _client() -> httpx.Client:
    return httpx.Client(timeout=25.0, headers=UA, follow_redirects=True)


def _arrow(desc: str, urls: list[str] | None = None, notes: str = "") -> LoopArrow:
    return LoopArrow(
        description=sanitize_untrusted(desc)[:800],
        evidence_urls=list(urls or [])[:8],
        evidence_notes=sanitize_untrusted(notes)[:600],
    )


def _score_0_5(x: float, lo: float, hi: float, higher_better: bool = True) -> float:
    if hi <= lo:
        return 0.0
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if not higher_better:
        t = 1.0 - t
    return round(5.0 * t, 2)


def _finalize_margin(loop: EconomicLoopSpec) -> EconomicLoopSpec:
    loop.contribution_margin_usd = loop.sell_price_usd - loop.marginal_cost_usd
    return loop


def hard_loop_gate(loop: EconomicLoopSpec) -> list[str]:
    """ALL must pass for finalist ranking."""
    fails: list[str] = []
    if loop.agent_role == AgentEconomicRole.WORKER:
        fails.append("worker_listing_out_of_scope")

    for name in ARROW_NAMES:
        arrow: LoopArrow = getattr(loop, name)
        if not arrow.description.strip():
            fails.append(f"missing_arrow_desc:{name}")
        if not arrow.evidence_urls and not arrow.evidence_notes.strip():
            fails.append(f"missing_arrow_evidence:{name}")

    buyer_blob = f"{loop.buyer_discovery.description} {loop.buyer_discovery.evidence_notes}".lower()
    if any(x in buyer_blob for x in ("upwork", "fiverr", "post a gig", "bespoke client")):
        fails.append("buyer_discovery_not_machine_reachable")

    fulfill_blob = f"{loop.fulfillment.description} {loop.fulfillment.evidence_notes}".lower()
    if any(x in fulfill_blob for x in ("meeting", "zoom", "revision round", "consult")):
        fails.append("fulfillment_not_digital_schema")

    pay_blob = f"{loop.verified_payment.description} {' '.join(loop.verified_payment.evidence_urls)}".lower()
    if not any(
        x in pay_blob
        for x in (
            "x402",
            "settlement",
            "payout",
            "affiliate",
            "cpa",
            "metered",
            "stripe",
            "usdc",
            "marketplace",
            "conversion",
            "payment",
        )
    ):
        fails.append("payment_path_not_observable")

    if loop.contribution_margin_usd <= 0:
        fails.append("nonpositive_contribution_margin")
    if loop.ongoing_human_minutes > 10:
        fails.append("ongoing_human_over_10_min")
    if loop.afternoon_budget_usd > Decimal("20"):
        fails.append("afternoon_budget_over_20")
    if not loop.afternoon_experiment.strip():
        fails.append("missing_afternoon_experiment")

    blob = f"{loop.summary} {loop.key_risk} {loop.name}".lower()
    if any(x in blob for x in ("fake review", "deceive", "impersonat", "spam farm")):
        fails.append("prohibited_or_deceptive")
    if any(x in blob for x in ("trading bot", "perps", "wash trade")):
        fails.append("trading_forbidden")
    return fails


def apply_loop_gate(loop: EconomicLoopSpec) -> EconomicLoopSpec:
    loop = _finalize_margin(loop)
    fails = hard_loop_gate(loop)
    loop.reject_reasons = fails
    loop.eligible = len(fails) == 0
    return loop


def seal_loop(loop: EconomicLoopSpec) -> EconomicLoopSpec:
    """Finalize margin → gate → score (correct order for ranking)."""
    return score_loop(apply_loop_gate(loop))


def score_loop(loop: EconomicLoopSpec) -> EconomicLoopSpec:
    """Interpretable 0–5 components; no explosive multiplicative scores."""
    margin = float(loop.contribution_margin_usd)
    url_arrows = 0
    for name in ARROW_NAMES:
        arrow: LoopArrow = getattr(loop, name)
        if arrow.evidence_urls:
            url_arrows += 1
    evidence_frac = url_arrows / float(len(ARROW_NAMES))
    setup_map = {
        SetupClass.A_NONE: 5.0,
        SetupClass.B_EXISTING: 4.0,
        SetupClass.C_AGENT_CREATABLE: 3.5,
        SetupClass.D_ONE_TIME_OWNER: 3.0,  # one-time OK under ADR-0003
        SetupClass.E_ONGOING_OWNER: 0.5,
    }
    scores = {
        "contribution_margin": _score_0_5(margin, 0, 0.05, True),
        "time_to_payment": _score_0_5(loop.time_to_first_payment_hours, 1, 168, False),
        "repeatability": _score_0_5(loop.repeatability, 0, 1, True),
        "automation": _score_0_5(loop.automation_pct, 0, 1, True),
        "low_setup_burden": setup_map.get(loop.setup_class, 2.0),
        "low_ongoing_human": _score_0_5(loop.ongoing_human_minutes, 0, 10, False),
        "evidence_quality": round(5.0 * evidence_frac, 2),
    }
    value = (scores["contribution_margin"] + scores["time_to_payment"] + scores["repeatability"] + scores["automation"] + scores["evidence_quality"]) / 5.0
    burden_ok = (scores["low_setup_burden"] + scores["low_ongoing_human"]) / 2.0
    loop.scores = scores
    loop.rank_score = round(0.65 * value + 0.35 * burden_ok, 3)
    return loop


def select_top_loops(loops: list[EconomicLoopSpec], k: int = 3) -> list[EconomicLoopSpec]:
    """Diverse finalists: prefer different scout families."""
    eligible = [L for L in loops if L.eligible]
    eligible.sort(key=lambda L: (L.rank_score, L.automation_pct), reverse=True)
    selected: list[EconomicLoopSpec] = []
    seen_scout: set[str] = set()
    for L in eligible:
        if L.scout in seen_scout:
            continue
        selected.append(L)
        seen_scout.add(L.scout)
        if len(selected) >= k:
            return selected
    for L in eligible:
        if L in selected:
            continue
        selected.append(L)
        if len(selected) >= k:
            break
    return selected[:k]


def justify_one_bootstrap(top: list[EconomicLoopSpec]) -> str:
    """At most ONE request — only if one-time payment rail is the sole blocker."""
    for L in top:
        if not L.eligible:
            continue
        exp = L.afternoon_experiment.lower()
        # Offline / draft afternoon probes do not justify setup yet.
        if any(
            k in exp
            for k in (
                "no public listing",
                "offline",
                "do not publish",
                "draft",
                "prototype",
                "document why",
                "re-probe",
                "estimate margin before",
            )
        ):
            continue
        if L.setup_class in (SetupClass.A_NONE, SetupClass.B_EXISTING, SetupClass.C_AGENT_CREATABLE):
            return f"No owner bootstrap requested. Prefer lowest-setup loop first: {L.name} ({L.scout})."
        if L.setup_class == SetupClass.D_ONE_TIME_OWNER and L.ongoing_human_minutes <= 10:
            return (
                "ONE bootstrap request (one-time payment rail is the final blocker):\n"
                f"- Loop: {L.name}\n"
                f"- Scout: {L.scout}\n"
                f"- Role: {L.agent_role.value}\n"
                f"- Setup: {L.setup_required}\n"
                f"- Afternoon experiment: {L.afternoon_experiment}\n"
                f"- Budget ≤ ${L.afternoon_budget_usd}\n"
                f"- Hypothesized margin: ${L.contribution_margin_usd}/txn\n"
                "Do not fund beyond the experiment budget until an external paid call/conversion "
                "is observed. Prefer an isolated wallet with a hard spending cap."
            )
    return "No owner bootstrap requested. Top afternoon experiments are offline/draft probes that do not yet require a payment rail."


# --- scouts -----------------------------------------------------------------


def scout_a2a_services() -> list[EconomicLoopSpec]:
    """Machine-to-machine paid APIs / MCP tools (x402 as one surface)."""
    loops: list[EconomicLoopSpec] = []
    discovery_hits: list[dict[str, Any]] = []
    discovery_url_used = ""
    with _client() as client:
        for url in X402_DISCOVERY_URLS:
            try:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                items = data if isinstance(data, list) else data.get("resources") or data.get("items") or []
                if isinstance(items, list) and items:
                    discovery_hits = items[:12]
                    discovery_url_used = url
                    break
            except Exception:
                continue
        # Volume concentration evidence from public scan site
        scan_snip = ""
        try:
            sr = client.get("https://www.x402scan.com/")
            if sr.status_code == 200:
                money = re.findall(r"\$[\d,]+(?:\.\d+)?", sr.text)[:8]
                scan_snip = f"x402scan_money_tokens={money[:5]}"
        except Exception:
            scan_snip = "x402scan_fetch_failed"

    if discovery_hits:
        # Build one gap-oriented merchant loop from observed paid catalog
        sample = discovery_hits[0]
        accepts = sample.get("accepts") or []
        amount_raw = ""
        if accepts and isinstance(accepts[0], dict):
            amount_raw = str(accepts[0].get("amount") or "")
        # amount often in atomic units; treat small display prices carefully
        sell = Decimal("0.025")
        cost = Decimal("0.008")
        loops.append(
            seal_loop(
                EconomicLoopSpec(
                    name="x402_niche_structured_feed_merchant",
                    scout="a2a_services",
                    agent_role=AgentEconomicRole.MERCHANT,
                    summary=(
                        "Sell a schema-bound HTTP/MCP research or structured-feed call "
                        "on an agent-payable catalog where buyers already pay tiny amounts; "
                        "target underserved niches with low seller volume."
                    ),
                    input_arrow=_arrow(
                        "Public web / open data + optional cheap upstream enrichment APIs",
                        ["https://www.x402scan.com/", discovery_url_used] if discovery_url_used else ["https://www.x402scan.com/"],
                        notes=f"sample_accepts_amount={amount_raw}; {scan_snip}",
                    ),
                    value_creation=_arrow(
                        "Deterministic extract/normalize into a fixed JSON schema; no bespoke prose",
                        [discovery_url_used] if discovery_url_used else ["https://github.com/x402-foundation/x402"],
                        notes="Value is machine-readable output per published schema",
                    ),
                    buyer_discovery=_arrow(
                        "Agent buyers discover via facilitator Bazaar /discovery/resources",
                        [discovery_url_used or "https://github.com/x402-foundation/x402/blob/main/docs/extensions/bazaar.mdx"],
                        notes=f"resources_seen={len(discovery_hits)}; {scan_snip}",
                    ),
                    transaction=_arrow(
                        "Buyer pays per call via x402 exact scheme (USDC); no API keys",
                        [discovery_url_used or "https://www.x402scan.com/"],
                        notes="Observable settlement on facilitator / scan explorers",
                    ),
                    fulfillment=_arrow(
                        "Return validated JSON body matching advertised output schema",
                        [discovery_url_used] if discovery_url_used else ["https://www.x402scan.com/"],
                        notes="Digital schema fulfillment; no meetings/revisions",
                    ),
                    verified_payment=_arrow(
                        "x402 settlement / facilitator receipt is the external ledger signal",
                        ["https://www.x402scan.com/"],
                        notes=scan_snip or "scan site reachable",
                    ),
                    marginal_cost=_arrow(
                        "Upstream fetch + small model/infra ≈ $0.008/call hypothesized",
                        ["https://www.x402scan.com/"],
                        notes="Hypothesis pending meter; afternoon probe measures actual cost",
                    ),
                    sell_price_usd=sell,
                    marginal_cost_usd=cost,
                    ongoing_human_minutes=0,
                    one_time_bootstrap_minutes=20,
                    setup_class=SetupClass.D_ONE_TIME_OWNER,
                    setup_required="Isolated wallet + x402 seller endpoint registration; hard spend cap",
                    afternoon_experiment=(
                        "Stand up one schema-bound stub endpoint behind x402; list on bazaar; attempt 1–3 paid test calls under $5 total; record settlements"
                    ),
                    afternoon_budget_usd=Decimal("15"),
                    automation_pct=0.92,
                    repeatability=0.85,
                    time_to_first_payment_hours=8,
                    key_risk="Demand concentrated; may get zero buyers; wallet setup required",
                )
            )
        )
    else:
        # Still emit a loop with discovery-doc evidence (may fail gate on thin payment observability)
        loops.append(
            seal_loop(
                EconomicLoopSpec(
                    name="x402_bazaar_probe_deferred",
                    scout="a2a_services",
                    agent_role=AgentEconomicRole.MERCHANT,
                    summary="x402 discovery endpoints were unreachable this run; keep as research surface.",
                    input_arrow=_arrow("n/a", notes="discovery HTTP non-200"),
                    value_creation=_arrow("n/a", notes="blocked on catalog fetch"),
                    buyer_discovery=_arrow(
                        "Bazaar /discovery/resources documented",
                        ["https://github.com/x402-foundation/x402/blob/main/docs/extensions/bazaar.mdx"],
                        notes="spec exists; live facilitator URL failed",
                    ),
                    transaction=_arrow("x402 exact payment", notes="not exercised"),
                    fulfillment=_arrow("JSON schema response", notes="not exercised"),
                    verified_payment=_arrow("settlement receipt", notes="not exercised"),
                    marginal_cost=_arrow("unknown", notes="not measured"),
                    sell_price_usd=Decimal("0.02"),
                    marginal_cost_usd=Decimal("0.02"),
                    afternoon_experiment="Re-probe facilitator URLs; do not deploy yet",
                    afternoon_budget_usd=Decimal("5"),
                    automation_pct=0.5,
                    key_risk="No live catalog this run",
                )
            )
        )
    return loops


def scout_data_refinery() -> list[EconomicLoopSpec]:
    evidence_urls: list[str] = []
    notes = ""
    with _client() as client:
        for url in (
            "https://data.gov/",
            "https://api.github.com/search/repositories?q=awesome+public+apis&per_page=1",
            "https://hunter.io/pricing",
        ):
            try:
                r = client.get(url)
                if r.status_code == 200:
                    evidence_urls.append(url)
                    notes += f" ok:{url.split('/')[2]}"
            except Exception:
                continue
    return [
        seal_loop(
            EconomicLoopSpec(
                name="public_change_diff_refinery",
                scout="data_refinery",
                agent_role=AgentEconomicRole.REFINERY,
                summary=("Acquire free public pages/datasets, compute structured diffs ('did this entity change materially since T-1?'), sell JSON answers."),
                input_arrow=_arrow(
                    "Public web pages / open government data (no proprietary scrape-resale)",
                    evidence_urls[:3] or ["https://data.gov/"],
                    notes=notes or "public sources",
                ),
                value_creation=_arrow(
                    "Normalize → hash/fingerprint → emit boolean+diff JSON schema",
                    evidence_urls[:2] or ["https://data.gov/"],
                    notes="Transformation creates the sellable unit; raw dump is not the product",
                ),
                buyer_discovery=_arrow(
                    "List as paid HTTP/MCP tool on agent catalogs OR sell via marketplace listing",
                    [
                        "https://github.com/x402-foundation/x402/blob/main/docs/extensions/bazaar.mdx",
                        "https://www.swarms.ai/marketplace",
                    ],
                    notes="Machine-reachable catalogs for agent buyers",
                ),
                transaction=_arrow(
                    "Per-call payment (x402 or marketplace checkout)",
                    ["https://www.x402scan.com/", "https://www.swarms.ai/marketplace"],
                    notes="Metered digital purchase",
                ),
                fulfillment=_arrow(
                    "Return {changed:bool, sources:[], fingerprint, as_of} JSON",
                    evidence_urls[:1] or ["https://data.gov/"],
                    notes="Schema-bound digital fulfillment",
                ),
                verified_payment=_arrow(
                    "Payment settlement or marketplace payout event",
                    ["https://www.x402scan.com/"],
                    notes="External ledger counts paid calls",
                ),
                marginal_cost=_arrow(
                    "HTTP fetch + compute ≈ $0.003–0.01/query hypothesized",
                    evidence_urls[:1] or ["https://data.gov/"],
                    notes="Measure in afternoon probe",
                ),
                sell_price_usd=Decimal("0.02"),
                marginal_cost_usd=Decimal("0.006"),
                ongoing_human_minutes=0,
                one_time_bootstrap_minutes=25,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                setup_required="Wallet or marketplace seller account (one-time)",
                afternoon_experiment=(
                    "Build offline prototype for 3 URLs; cost the fetches; draft schema; estimate margin before any paid listing ($<10 compute)"
                ),
                afternoon_budget_usd=Decimal("10"),
                automation_pct=0.9,
                repeatability=0.9,
                time_to_first_payment_hours=24,
                key_risk="License/ToS on sources; cold-start demand",
            )
        )
    ]


def scout_referral_broker() -> list[EconomicLoopSpec]:
    pages = [
        ("https://www.shopify.com/affiliates", "Shopify affiliates"),
        ("https://affiliate-program.amazon.com/", "Amazon Associates"),
        ("https://www.digitalocean.com/open-source/credits/referral", "DigitalOcean referral"),
    ]
    found: list[tuple[str, str, str]] = []
    with _client() as client:
        for url, name in pages:
            try:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                rates = re.findall(
                    r"(?:\d+%|\$\s?[\d,]+(?:\.\d+)?)\s*(?:commission|CPA|credit|revshare)?",
                    r.text,
                    re.I,
                )[:6]
                found.append((url, name, ",".join(rates[:4]) or "rates_unparsed"))
            except Exception:
                continue
    if not found:
        return []
    url, name, rates = found[0]
    return [
        seal_loop(
            EconomicLoopSpec(
                name="agent_tool_affiliate_broker",
                scout="referral_broker",
                agent_role=AgentEconomicRole.BROKER,
                summary=(f"Detect when an agent workflow needs {name}-class tooling; insert disclosed tracked referral; collect CPA/revshare."),
                input_arrow=_arrow(
                    "Agent intent / tool-need signals from public docs & marketplace queries",
                    [url],
                    notes=f"program={name}",
                ),
                value_creation=_arrow(
                    "Match need → disclosed referral link; no fake reviews",
                    [url],
                    notes=f"published_rates_sample={rates}",
                ),
                buyer_discovery=_arrow(
                    "Agent catalogs / docs that accept affiliate deep links",
                    [url, "https://www.swarms.ai/marketplace"],
                    notes="Broker surfaces where agents already shop for tools",
                ),
                transaction=_arrow(
                    "Attributed click → qualified conversion per program terms",
                    [url],
                    notes="Affiliate conversion is the paid event",
                ),
                fulfillment=_arrow(
                    "Serve correct tracked URL + disclosure metadata as JSON",
                    [url],
                    notes="Digital fulfillment of the referral packet",
                ),
                verified_payment=_arrow(
                    "Affiliate dashboard conversion / CPA payout line",
                    [url],
                    notes="External payout report is the ledger signal",
                ),
                marginal_cost=_arrow(
                    "Near-zero marginal cost per referral packet",
                    [url],
                    notes="Cost is tracking infra only",
                ),
                sell_price_usd=Decimal("5"),  # expected CPA sample, not list price
                marginal_cost_usd=Decimal("0.05"),
                ongoing_human_minutes=2,
                one_time_bootstrap_minutes=30,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                setup_required=f"{name} affiliate account + tax identity (one-time)",
                afternoon_experiment=(
                    f"Create affiliate account for {name} if needed; generate one tracked link; document conversion event fields; no paid ads ($0–5)"
                ),
                afternoon_budget_usd=Decimal("5"),
                automation_pct=0.85,
                repeatability=0.75,
                time_to_first_payment_hours=72,
                key_risk="Attribution delays; program bans undisclosed automation",
            )
        )
    ]


def scout_digital_marketplace() -> list[EconomicLoopSpec]:
    url = "https://www.swarms.ai/marketplace"
    snip = ""
    with _client() as client:
        try:
            r = client.get(url)
            if r.status_code == 200:
                snip = sanitize_untrusted(re.sub(r"\s+", " ", r.text))[:400]
                listings = len(re.findall(r"listing|MCP|agent|skill|prompt", r.text, re.I))
                snip = f"keyword_hits≈{listings}; " + snip[:300]
        except Exception:
            snip = "marketplace_fetch_failed"
    return [
        seal_loop(
            EconomicLoopSpec(
                name="swarms_mcp_tool_merchant",
                scout="digital_marketplace",
                agent_role=AgentEconomicRole.MERCHANT,
                summary=("Package a reusable MCP/tool/skill with fixed I/O contract; list on an agent marketplace with published seller payout share."),
                input_arrow=_arrow(
                    "Public APIs / local deterministic logic as tool backend",
                    [url],
                    notes=snip[:200],
                ),
                value_creation=_arrow(
                    "Ship MCP server or skill with schema + examples",
                    [url],
                    notes="Product is the tool artifact, not bespoke labor",
                ),
                buyer_discovery=_arrow(
                    "Marketplace listing discoverable by agents/builders",
                    [url],
                    notes="Machine-reachable catalog page",
                ),
                transaction=_arrow(
                    "Marketplace checkout / purchase of tool listing",
                    [url],
                    notes="Seller payout ~90% advertised on Swarms marketing",
                ),
                fulfillment=_arrow(
                    "Deliver downloadable MCP/skill package or endpoint credentials via marketplace",
                    [url],
                    notes="Digital goods fulfillment",
                ),
                verified_payment=_arrow(
                    "Marketplace seller payout event",
                    [url],
                    notes="External payout is the signal",
                ),
                marginal_cost=_arrow(
                    "Hosting per call or near-zero for downloadable skill",
                    [url],
                    notes="Prefer downloadable skill for near-zero COGS",
                ),
                sell_price_usd=Decimal("9"),
                marginal_cost_usd=Decimal("0.5"),
                ongoing_human_minutes=5,
                one_time_bootstrap_minutes=40,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                setup_required="Marketplace seller account (one-time)",
                afternoon_experiment=(
                    "Draft one MCP skill README+schema; price at $5–15; confirm listing requirements; do not publish until policy review ($0)"
                ),
                afternoon_budget_usd=Decimal("5"),
                automation_pct=0.8,
                repeatability=0.7,
                time_to_first_payment_hours=48,
                key_risk="Marketplace quality bar / cold start; payout terms change",
            )
        )
    ]


def scout_autonomous_monitoring() -> list[EconomicLoopSpec]:
    urls = [
        "https://visualping.io/",
        "https://distill.io/",
        "https://www.changedetection.io/",
    ]
    live: list[str] = []
    with _client() as client:
        for u in urls:
            try:
                r = client.get(u)
                if r.status_code == 200:
                    live.append(u)
            except Exception:
                continue
    return [
        seal_loop(
            EconomicLoopSpec(
                name="material_change_alert_api",
                scout="autonomous_monitoring",
                agent_role=AgentEconomicRole.MERCHANT,
                summary=(
                    "Sell a machine-callable 'entity changed since yesterday?' endpoint; "
                    "compete with human-oriented page monitors by offering agent-native JSON."
                ),
                input_arrow=_arrow(
                    "Target URLs/APIs supplied by caller; public pages only by default",
                    live[:3] or urls[:1],
                    notes="Competitor monitors prove willingness to pay for change signals",
                ),
                value_creation=_arrow(
                    "Poll → diff → structured alert JSON with confidence + citations",
                    live[:2] or urls[:1],
                    notes="Agent-native schema vs email screenshots",
                ),
                buyer_discovery=_arrow(
                    "List on agent-payable catalogs + docs SEO for 'change detection API'",
                    [
                        "https://github.com/x402-foundation/x402/blob/main/docs/extensions/bazaar.mdx",
                        *(live[:1] or urls[:1]),
                    ],
                    notes="Agents discover via catalogs; humans via docs",
                ),
                transaction=_arrow(
                    "Per-check micropayment or subscription meter",
                    live[:1] or ["https://www.x402scan.com/"],
                    notes="x402 per-call or classic metered billing",
                ),
                fulfillment=_arrow(
                    "HTTP 200 + JSON alert body; optional webhook",
                    live[:1] or urls[:1],
                    notes="Digital schema fulfillment",
                ),
                verified_payment=_arrow(
                    "Payment settlement or Stripe meter event",
                    ["https://www.x402scan.com/"],
                    notes="External payment event",
                ),
                marginal_cost=_arrow(
                    "Fetch+diff cost ≈ $0.002–0.01 per check",
                    live[:1] or urls[:1],
                    notes="Afternoon probe measures actual",
                ),
                sell_price_usd=Decimal("0.01"),
                marginal_cost_usd=Decimal("0.003"),
                ongoing_human_minutes=0,
                one_time_bootstrap_minutes=25,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                setup_required="Payment rail (wallet or Stripe) one-time",
                afternoon_experiment=("Implement local diff for 5 URLs; time/cost the loop; draft OpenAPI schema; no public listing yet ($<5)"),
                afternoon_budget_usd=Decimal("5"),
                automation_pct=0.93,
                repeatability=0.95,
                time_to_first_payment_hours=24,
                key_risk="Crowded monitor market; need agent-native distribution",
            )
        )
    ]


def scout_digital_cashflow_assets() -> list[EconomicLoopSpec]:
    """OPERATOR: only include if a <$20 probe exists — prefer deferral."""
    url = "https://gumroad.com/"
    ok = False
    with _client() as client:
        try:
            r = client.get(url)
            ok = r.status_code == 200
        except Exception:
            ok = False
    # Deliberately thin OPERATOR candidate — likely fails gate or ranks low
    return [
        seal_loop(
            EconomicLoopSpec(
                name="digital_asset_operator_deferred",
                scout="digital_cashflow_assets",
                agent_role=AgentEconomicRole.OPERATOR,
                summary=("Owning an existing cash-flow asset is later-phase; afternoon probe limited to surveying self-serve digital storefronts."),
                input_arrow=_arrow(
                    "Survey self-serve digital product platforms",
                    [url] if ok else [],
                    notes="reachable" if ok else "unreachable",
                ),
                value_creation=_arrow(
                    "N/A own/improve asset — deferred",
                    notes="No asset owned yet",
                ),
                buyer_discovery=_arrow(
                    "Platform storefront discovery",
                    [url] if ok else [],
                    notes="human storefronts; weak agent discovery",
                ),
                transaction=_arrow(
                    "Platform checkout",
                    [url] if ok else [],
                    notes="payment exists on platform",
                ),
                fulfillment=_arrow(
                    "Digital download",
                    [url] if ok else [],
                    notes="digital goods",
                ),
                verified_payment=_arrow(
                    "Platform payout",
                    [url] if ok else [],
                    notes="seller payout",
                ),
                marginal_cost=_arrow(
                    "Unknown without an owned asset",
                    notes="cannot hypothesize positive margin without asset",
                ),
                sell_price_usd=Decimal("1"),
                marginal_cost_usd=Decimal("1"),
                ongoing_human_minutes=15,
                setup_class=SetupClass.E_ONGOING_OWNER,
                setup_required="Acquire/operate an asset (out of Gen-0 afternoon scope)",
                afternoon_experiment="Document why OPERATOR loops wait until after merchant probes ($0)",
                afternoon_budget_usd=Decimal("0"),
                automation_pct=0.4,
                repeatability=0.3,
                time_to_first_payment_hours=168,
                key_risk="No owned cash-flow asset; ongoing human likely",
            )
        )
    ]


def discover_economic_loops() -> list[EconomicLoopSpec]:
    """Run six scouts in parallel; return gated+scored loops."""
    scouts = [
        scout_a2a_services,
        scout_data_refinery,
        scout_referral_broker,
        scout_digital_marketplace,
        scout_autonomous_monitoring,
        scout_digital_cashflow_assets,
    ]
    loops: list[EconomicLoopSpec] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fn): fn.__name__ for fn in scouts}
        for fut in as_completed(futs):
            try:
                batch = fut.result()
            except Exception as exc:  # noqa: BLE001
                # Emit a failed scout placeholder (ineligible)
                loops.append(
                    seal_loop(
                        EconomicLoopSpec(
                            name=f"scout_failed_{futs[fut]}",
                            scout=futs[fut],
                            agent_role=AgentEconomicRole.MERCHANT,
                            summary=f"Scout error: {exc}",
                            input_arrow=_arrow("error", notes=str(exc)[:200]),
                            value_creation=_arrow("error", notes="failed"),
                            buyer_discovery=_arrow("error", notes="failed"),
                            transaction=_arrow("error", notes="failed"),
                            fulfillment=_arrow("error", notes="failed"),
                            verified_payment=_arrow("error", notes="failed"),
                            marginal_cost=_arrow("error", notes="failed"),
                            sell_price_usd=Decimal("0"),
                            marginal_cost_usd=Decimal("1"),
                            afternoon_experiment="retry scout",
                            afternoon_budget_usd=Decimal("0"),
                            key_risk=str(exc)[:200],
                        )
                    )
                )
                continue
            loops.extend(batch)
    loops.sort(key=lambda L: (L.eligible, L.rank_score), reverse=True)
    return loops


def loops_report(loops: list[EconomicLoopSpec], top: list[EconomicLoopSpec]) -> dict[str, Any]:
    return {
        "campaign": "ECONOMIC_LOOP_DISCOVERY_V1",
        "worker_listing_hunt": "CLOSED",
        "finding_preserved": "ZERO_SETUP_OBJECTIVE_TASK_MARKET = SPARSE",
        "considered": [json.loads(L.model_dump_json()) for L in loops],
        "top3": [json.loads(L.model_dump_json()) for L in top],
        "bootstrap_request": justify_one_bootstrap(top),
        "note": (
            "Gen-0 unit is the economic loop (ADR-0003). "
            "Optimize zero ongoing human involvement; one-time setup allowed. "
            "Discovery only — no treasury spend in this campaign."
        ),
    }
