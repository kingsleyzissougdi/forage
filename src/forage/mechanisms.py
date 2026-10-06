"""Generation-0 monetization mechanism discovery, eligibility, and ranking.

Not a business-idea brainstormer. Public evidence required. No ML.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from forage.adapters.gateway import PublicWebResearchAdapter, ResearchHit, ResearchPort, sanitize_untrusted
from forage.models import MechanismRole, MechanismSpec, SetupClass

# Broad mechanism-oriented queries — not freelance/SaaS/platform defaults
MECHANISM_QUERIES = [
    "bug bounty payout rewarded CVE",
    "Immunefi bounty paid hacker",
    "Gitcoin bounty completed payment",
    "Challenge.gov prize winner awarded",
    "open source software bounty dollar reward",
    "dataset marketplace sold download",
    "affiliate commission disclosed conversion",
    "referral cash reward program payout",
    "API usage billing metered revenue",
    "mechanical turk HIT payment rate",
    "Kaggle competition prize awarded",
    "Stack Overflow bounty awarded reputation",
    "uptime monitoring alert paid subscription pricing",
    "RSS keyword alert paid newsletter",
    "public domain data product sold",
    "Chrome extension paid users revenue",
    "open bounty board dollar reward criteria",
    "security vulnerability disclosure paid reward",
]


ILLEGAL_OR_FORBIDDEN = (
    "illegal",
    "dark web",
    "stolen",
    "fake review",
    "click farm",
    "spam blast",
    "bypass captcha illegally",
    "identity theft",
    "insider trading",
    "leverage trading bot",
    "margin trade",
)


@dataclass
class RankRow:
    mechanism: MechanismSpec
    numerator: float
    denominator: float
    score: float


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def setup_friction(setup: SetupClass) -> float:
    return {
        SetupClass.A_NONE: 0.15,
        SetupClass.B_EXISTING: 0.25,
        SetupClass.C_AGENT_CREATABLE: 0.35,
        SetupClass.D_ONE_TIME_OWNER: 0.55,
        SetupClass.E_ONGOING_OWNER: 0.95,
    }[setup]


def eligibility_check(m: MechanismSpec) -> MechanismSpec:
    reasons: list[str] = []
    blob = " ".join(
        [
            m.description,
            m.required_agent_action,
            m.acceptance_method,
            m.third_party_dependency,
            " ".join(m.blockers),
        ]
    ).lower()
    for bad in ILLEGAL_OR_FORBIDDEN:
        if bad in blob:
            reasons.append(f"forbidden_pattern:{bad}")
    if m.liability_reputation_risk >= 0.9:
        reasons.append("liability_reputation_too_high")
    if m.subjective_quality_risk >= 0.9 and m.automation_feasibility < 0.5:
        reasons.append("subjective_bespoke_with_low_automation")
    if m.time_to_feedback_hours > 24 * 60 and float(m.expected_txn_value_usd) < 500:
        reasons.append("feedback_months_without_compelling_economics")
    if m.setup_class == SetupClass.E_ONGOING_OWNER and m.ongoing_human_minutes_per_txn >= 10:
        reasons.append("ongoing_owner_heavy")
    if "trading" in blob and ("autonomous" in blob or "bot" in blob):
        reasons.append("autonomous_financial_trading")
    if m.role == MechanismRole.RESEARCH_INFRASTRUCTURE:
        # Still "eligible" as a feed, but never capital-finalist (score forced 0).
        pass
    if not m.evidence_urls and not m.evidence_notes:
        reasons.append("no_external_evidence")
    m.reject_reasons = reasons
    m.eligible = len(reasons) == 0
    return m


def score_mechanism(m: MechanismSpec) -> MechanismSpec:
    """Transparent 0–5 component rubric. Infrastructure cannot win capital allocation."""
    if m.role == MechanismRole.RESEARCH_INFRASTRUCTURE:
        m.score_components = {
            "note": 0.0,
            "capital_eligible": 0.0,
            "role": 0.0,
        }
        m.score = 0.0
        return m

    upside = _score05(float(m.expected_txn_value_usd), 0, 200, True)
    speed = _score05(m.time_to_feedback_hours, 1, 24 * 14, False)
    low_cost = _score05(float(m.startup_cost_usd), 0, 50, False)
    low_ongoing = _score05(m.ongoing_human_minutes_per_txn, 0, 30, False)
    obj_ver = _score05(1.0 - m.subjective_quality_risk, 0, 1, True)
    automation = _score05(m.automation_feasibility, 0, 1, True)
    repeat = _score05(m.repeatability, 0, 1, True)
    low_setup = {
        SetupClass.A_NONE: 5.0,
        SetupClass.B_EXISTING: 4.0,
        SetupClass.C_AGENT_CREATABLE: 3.5,
        SetupClass.D_ONE_TIME_OWNER: 2.0,
        SetupClass.E_ONGOING_OWNER: 0.5,
    }[m.setup_class]
    low_liability = _score05(1.0 - m.liability_reputation_risk, 0, 1, True)

    components = {
        "expected_upside_0_5": upside,
        "speed_0_5": speed,
        "low_cost_0_5": low_cost,
        "low_ongoing_human_0_5": low_ongoing,
        "objective_verifiability_0_5": obj_ver,
        "automation_0_5": automation,
        "repeatability_0_5": repeat,
        "low_setup_friction_0_5": low_setup,
        "low_liability_0_5": low_liability,
        "capital_eligible": 1.0,
    }
    value = (upside + speed + obj_ver + automation + repeat) / 5.0
    burden_ok = (low_cost + low_ongoing + low_setup + low_liability) / 4.0
    m.score_components = {k: round(v, 3) for k, v in components.items()}
    m.score = round(0.65 * value + 0.35 * burden_ok, 3)
    return m


def _score05(x: float, lo: float, hi: float, higher_better: bool) -> float:
    if hi <= lo:
        return 0.0
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if not higher_better:
        t = 1.0 - t
    return round(5.0 * t, 3)


def _seed_mechanisms_from_hits(hits: list[ResearchHit]) -> list[MechanismSpec]:
    """Map diverse public hits into mechanism classes. Evidence-backed only."""
    text_all = " ".join(f"{h.title} {h.snippet}" for h in hits).lower()
    by_url = {h.url: h for h in hits if h.url}

    def urls_matching(*keys: str) -> list[str]:
        out: list[str] = []
        for h in hits:
            t = f"{h.title} {h.snippet}".lower()
            if any(k in t for k in keys) and h.url:
                out.append(h.url)
        return list(dict.fromkeys(out))[:6]

    specs: list[MechanismSpec] = []

    def add(m: MechanismSpec) -> None:
        if m.evidence_urls or m.evidence_notes:
            specs.append(m)

    # --- objective bounty / reward markets ---
    if any(k in text_all for k in ("bounty", "immunefi", "bug bounty", "vulnerability")):
        add(
            MechanismSpec(
                mechanism_type="objective_security_bounty",
                description="Public security/bug bounty programs pay for verified vulnerabilities under published rules.",
                payer="Program sponsor (vendor / protocol / platform)",
                paid_event="Bounty payout after triage accepts a valid report",
                evidence_urls=urls_matching("bounty", "immunefi", "vulnerability", "cve") or [h.url for h in hits if h.url][:3],
                evidence_notes="Public pages discuss paid bug bounties and rewarded reports.",
                required_agent_action="Find in-scope assets, reproduce issues, submit report per program rules",
                acceptance_method="Program triage decision + payout record (objective relative to scope)",
                time_to_feedback_hours=72,
                startup_cost_usd=Decimal("5"),
                expected_txn_value_usd=Decimal("250"),
                repeatability=0.7,
                automation_feasibility=0.55,
                ongoing_human_minutes_per_txn=5,
                one_time_bootstrap_minutes=20,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Bounty platform account (HackerOne/Immunefi/etc.)",
                subjective_quality_risk=0.35,
                liability_reputation_risk=0.55,
                platform_account_risk=0.4,
                fulfillment_complexity=0.7,
                cheapest_validation="Enumerate live public programs with published $ ranges; attempt one in-scope read-only recon report draft (no exploit)",
                blockers=["owner_identity_for_payout", "careful_scope_compliance"],
            )
        )

    if any(k in text_all for k in ("gitcoin", "open source bounty", "oss bounty")):
        add(
            MechanismSpec(
                mechanism_type="open_source_task_bounty",
                description="Open-source bounties pay for merged PRs / completed issues with stated acceptance criteria.",
                payer="Bounty funder / DAO / maintainer",
                paid_event="Bounty marked complete + payment released after acceptance criteria met",
                evidence_urls=urls_matching("gitcoin", "bounty", "open source"),
                evidence_notes="Public OSS bounty markets advertise dollar rewards for defined tasks.",
                required_agent_action="Claim/complete a scoped issue; open PR meeting acceptance tests",
                acceptance_method="Maintainer merge / automated checks / bounty platform status",
                time_to_feedback_hours=96,
                startup_cost_usd=Decimal("3"),
                expected_txn_value_usd=Decimal("150"),
                repeatability=0.75,
                automation_feasibility=0.6,
                ongoing_human_minutes_per_txn=10,
                one_time_bootstrap_minutes=25,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="GitHub + bounty platform wallet/account",
                subjective_quality_risk=0.4,
                liability_reputation_risk=0.3,
                platform_account_risk=0.35,
                fulfillment_complexity=0.65,
                cheapest_validation="List 5 open public bounties with $ amount + acceptance criteria; attempt one tiny docs/test PR if policy allows",
                blockers=["github_identity", "wallet_payout"],
            )
        )

    if any(k in text_all for k in ("challenge.gov", "prize", "kaggle", "competition prize")):
        add(
            MechanismSpec(
                mechanism_type="public_prize_challenge",
                description="Government/industry challenges pay prizes for objectively scored submissions.",
                payer="Challenge sponsor (gov / corp)",
                paid_event="Prize award after published evaluation criteria",
                evidence_urls=urls_matching("challenge.gov", "prize", "kaggle", "awarded"),
                evidence_notes="Public challenge/prize pages show historical awards and criteria.",
                required_agent_action="Submit compliant entry meeting stated metrics",
                acceptance_method="Published scoring rubric / leaderboard",
                time_to_feedback_hours=24 * 30,
                startup_cost_usd=Decimal("10"),
                expected_txn_value_usd=Decimal("1000"),
                repeatability=0.4,
                automation_feasibility=0.5,
                ongoing_human_minutes_per_txn=15,
                one_time_bootstrap_minutes=40,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Challenge portal registration",
                subjective_quality_risk=0.3,
                liability_reputation_risk=0.25,
                platform_account_risk=0.3,
                fulfillment_complexity=0.75,
                cheapest_validation="Pull open Challenge.gov / similar listings with prize amounts and deadlines; pick one with machine-checkable metric",
                blockers=["registration", "long_feedback_cycle"],
            )
        )

    if any(k in text_all for k in ("mechanical turk", "mturk", "microwork", "paid task", "hit payment")):
        add(
            MechanismSpec(
                mechanism_type="microtask_reward_market",
                description="Crowdsourced task markets pay fixed amounts for objectively checked HITs/tasks.",
                payer="Task requester via platform",
                paid_event="Task accepted → worker payment credited",
                evidence_urls=urls_matching("mechanical turk", "mturk", "hit", "payment"),
                evidence_notes="Public discussion of HIT payment rates and acceptance.",
                required_agent_action="Complete listed tasks meeting automated/requester checks",
                acceptance_method="Platform acceptance + balance credit",
                time_to_feedback_hours=24,
                startup_cost_usd=Decimal("0"),
                expected_txn_value_usd=Decimal("5"),
                repeatability=0.9,
                automation_feasibility=0.7,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=30,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Worker account (often KYC)",
                subjective_quality_risk=0.25,
                liability_reputation_risk=0.2,
                platform_account_risk=0.5,
                fulfillment_complexity=0.35,
                cheapest_validation="Document live public task boards with $ rates; if no account, stop at NEEDS_BOOTSTRAP after verifying inventory",
                blockers=["worker_kyc"],
            )
        )

    # --- affiliate / referral (evidence-based only) ---
    if any(k in text_all for k in ("affiliate", "commission", "referral reward", "referral cash")):
        add(
            MechanismSpec(
                mechanism_type="affiliate_conversion",
                description="Affiliate/referral programs pay on tracked conversions under published rates.",
                payer="Merchant / network",
                paid_event="Tracked sale/signup attributed to affiliate ID → commission",
                evidence_urls=urls_matching("affiliate", "commission", "referral"),
                evidence_notes="Public pages disclose commissions and conversion-based pay.",
                required_agent_action="Publish compliant content/links; drive measurable attributed traffic",
                acceptance_method="Network conversion pixel / payout report",
                time_to_feedback_hours=168,
                startup_cost_usd=Decimal("5"),
                expected_txn_value_usd=Decimal("40"),
                repeatability=0.85,
                automation_feasibility=0.65,
                ongoing_human_minutes_per_txn=2,
                one_time_bootstrap_minutes=35,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Affiliate network account + tax forms",
                subjective_quality_risk=0.35,
                liability_reputation_risk=0.35,
                platform_account_risk=0.45,
                fulfillment_complexity=0.45,
                cheapest_validation=(
                    "Pick one disclosed-rate program; produce one compliant landing sample; measure click→pixel if account exists, else NEEDS_BOOTSTRAP"
                ),
                blockers=["affiliate_account", "disclosure_compliance"],
            )
        )

    # --- self-service information / data products ---
    if any(k in text_all for k in ("dataset", "data marketplace", "data product", "csv", "download")):
        add(
            MechanismSpec(
                mechanism_type="self_service_data_product",
                description="Sell fixed digital datasets/reports with listed prices; no bespoke client scope.",
                payer="Anonymous self-serve buyer",
                paid_event="Checkout completed for SKU download",
                evidence_urls=urls_matching("dataset", "marketplace", "data product", "sold"),
                evidence_notes="Public marketplaces and posts show data products changing hands.",
                required_agent_action="Generate one fixed SKU; list at fixed price; fulfill digitally",
                acceptance_method="Payment provider charge succeeded",
                time_to_feedback_hours=72,
                startup_cost_usd=Decimal("8"),
                expected_txn_value_usd=Decimal("29"),
                repeatability=0.8,
                automation_feasibility=0.85,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=45,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Payment + listing surface (not pre-chosen)",
                subjective_quality_risk=0.3,
                liability_reputation_risk=0.35,
                platform_account_risk=0.4,
                fulfillment_complexity=0.4,
                cheapest_validation="Publish one sample excerpt publicly; count inbound interest OR list SKU once payment identity exists",
                blockers=["payment_identity", "listing_surface"],
            )
        )

    # --- autonomous monitoring / alerts ---
    if any(k in text_all for k in ("monitor", "alert", "uptime", "rss", "keyword alert", "subscription")):
        add(
            MechanismSpec(
                mechanism_type="autonomous_monitoring_alert",
                description="Paid monitoring/alert subscriptions for objective events (uptime, keyword, price change).",
                payer="Subscriber",
                paid_event="Subscription charge / prepaid alert plan purchase",
                evidence_urls=urls_matching("monitor", "alert", "uptime", "subscription", "pricing"),
                evidence_notes="Public pricing pages for monitoring/alert products.",
                required_agent_action="Run watcher; deliver alerts via email/webhook; bill period",
                acceptance_method="Payment + delivery logs (objective)",
                time_to_feedback_hours=120,
                startup_cost_usd=Decimal("10"),
                expected_txn_value_usd=Decimal("15"),
                repeatability=0.9,
                automation_feasibility=0.9,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=40,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Billing + delivery channel",
                subjective_quality_risk=0.2,
                liability_reputation_risk=0.3,
                platform_account_risk=0.35,
                fulfillment_complexity=0.5,
                cheapest_validation="Ship one free public alert sample (email/RSS) for a narrow keyword; measure subscribe intent before billing",
                blockers=["billing_setup"],
            )
        )

    # --- lead/referral with objective output ---
    if any(k in text_all for k in ("lead", "referral", "prospect list")):
        add(
            MechanismSpec(
                mechanism_type="objective_lead_referral",
                description="Paid for objectively defined lead packets (fields + public sources), not bespoke consulting.",
                payer="Buyer of lead packet SKU or CPA partner",
                paid_event="Payment for N verified public-contact leads OR CPA conversion",
                evidence_urls=urls_matching("lead", "referral", "prospect"),
                evidence_notes="Public demand for lead lists / referral payouts.",
                required_agent_action="Assemble N rows meeting field checklist from public sources",
                acceptance_method="Checklist validation + payment (or CPA pixel)",
                time_to_feedback_hours=96,
                startup_cost_usd=Decimal("5"),
                expected_txn_value_usd=Decimal("50"),
                repeatability=0.75,
                automation_feasibility=0.7,
                ongoing_human_minutes_per_txn=3,
                one_time_bootstrap_minutes=30,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Listing/payment or CPA program",
                subjective_quality_risk=0.45,
                liability_reputation_risk=0.5,
                platform_account_risk=0.4,
                fulfillment_complexity=0.5,
                cheapest_validation="Produce one 10-row public-source sample with checklist; solicit prepaid order without custom negotiation",
                blockers=["payment_or_cpa", "privacy_compliance"],
            )
        )

    # --- marketplace with explicit acceptance ---
    if any(k in text_all for k in ("marketplace", "accepted", "reward criteria", "stack overflow bounty")):
        add(
            MechanismSpec(
                mechanism_type="explicit_criteria_marketplace",
                description="Markets/platforms where acceptance is rule-based (tests, rubrics, bounties), not taste.",
                payer="Marketplace buyer or bounty setter",
                paid_event="Acceptance event triggers payout",
                evidence_urls=urls_matching("marketplace", "bounty", "accepted", "criteria"),
                evidence_notes="Public marketplaces/bounties with explicit acceptance.",
                required_agent_action="Submit work meeting published automated/rubric checks",
                acceptance_method="Automated tests / explicit rubric pass",
                time_to_feedback_hours=48,
                startup_cost_usd=Decimal("4"),
                expected_txn_value_usd=Decimal("75"),
                repeatability=0.7,
                automation_feasibility=0.65,
                ongoing_human_minutes_per_txn=5,
                one_time_bootstrap_minutes=25,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Marketplace seller account",
                subjective_quality_risk=0.3,
                liability_reputation_risk=0.3,
                platform_account_risk=0.45,
                fulfillment_complexity=0.55,
                cheapest_validation="Find 3 live listings with machine-checkable acceptance; complete one smallest eligible unit",
                blockers=["seller_account"],
            )
        )

    # --- digital asset without bespoke client work ---
    if any(k in text_all for k in ("extension", "digital download", "template", "pack", "asset")):
        add(
            MechanismSpec(
                mechanism_type="digital_asset_sku",
                description="Generate and sell fixed digital assets/templates without per-client customization.",
                payer="Self-serve purchaser",
                paid_event="SKU purchase",
                evidence_urls=urls_matching("extension", "download", "template", "pack", "chrome"),
                evidence_notes="Public evidence of paid digital downloads / extensions.",
                required_agent_action="Create one fixed asset; list; auto-deliver file",
                acceptance_method="Payment succeeded",
                time_to_feedback_hours=120,
                startup_cost_usd=Decimal("8"),
                expected_txn_value_usd=Decimal("19"),
                repeatability=0.85,
                automation_feasibility=0.8,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=50,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Store / payment listing",
                subjective_quality_risk=0.35,
                liability_reputation_risk=0.3,
                platform_account_risk=0.5,
                fulfillment_complexity=0.45,
                cheapest_validation="Ship one free sample asset publicly; measure downloads/stars before paid listing",
                blockers=["store_account"],
            )
        )

    # --- API / metered usage (if evidence) ---
    if any(k in text_all for k in ("api", "metered", "usage based", "usage-based")):
        add(
            MechanismSpec(
                mechanism_type="metered_api_usage",
                description="Charge for metered API calls that deliver a fixed computational service.",
                payer="API customer",
                paid_event="Usage invoice paid / prepaid credits consumed",
                evidence_urls=urls_matching("api", "metered", "usage"),
                evidence_notes="Public metered API pricing models.",
                required_agent_action="Expose one narrow API; bill usage",
                acceptance_method="Payment processor + usage logs",
                time_to_feedback_hours=168,
                startup_cost_usd=Decimal("20"),
                expected_txn_value_usd=Decimal("50"),
                repeatability=0.9,
                automation_feasibility=0.85,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=90,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Hosting + billing",
                subjective_quality_risk=0.2,
                liability_reputation_risk=0.35,
                platform_account_risk=0.4,
                fulfillment_complexity=0.7,
                cheapest_validation="Offer one free rate-limited endpoint publicly; measure authenticated call intent before billing",
                blockers=["billing", "hosting_identity"],
            )
        )

    # --- always include a no-account observational inventory probe when public boards exist ---
    bounty_urls = urls_matching("bounty", "immunefi", "hackerone", "vulnerability")
    challenge_urls = urls_matching("challenge.gov", "prize", "kaggle")
    if challenge_urls or "challenge.gov" in text_all or any("challenge.gov" in (h.url or "") for h in hits):
        add(
            MechanismSpec(
                mechanism_type="public_challenge_inventory_signal",
                role=MechanismRole.RESEARCH_INFRASTRUCTURE,
                description=("Read-only: publicly listed open challenges with $ prizes as an economic inventory signal (no account)."),
                payer="N/A observational — prize sponsors exist publicly",
                paid_event="Observational: confirmed open prize listings with amounts",
                evidence_urls=challenge_urls or ["https://www.challenge.gov/"],
                evidence_notes="Challenge.gov is a public registry of prize competitions.",
                required_agent_action="Fetch open challenges; record prize amounts/criteria into ledger",
                acceptance_method="HTTP 200 + structured prize fields observed",
                time_to_feedback_hours=1,
                startup_cost_usd=Decimal("0"),
                expected_txn_value_usd=Decimal("50"),  # learning / option value, not cash
                repeatability=0.5,
                automation_feasibility=0.95,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=0,
                setup_class=SetupClass.A_NONE,
                third_party_dependency="None (public web)",
                subjective_quality_risk=0.05,
                liability_reputation_risk=0.05,
                platform_account_risk=0.05,
                fulfillment_complexity=0.1,
                cheapest_validation="GET public challenge listings; ledger ≥3 open prizes with $ amounts",
                blockers=[],
            )
        )
    if bounty_urls or any(k in text_all for k in ("immunefi", "hackerone", "bug bounty")):
        add(
            MechanismSpec(
                mechanism_type="public_bounty_inventory_signal",
                role=MechanismRole.RESEARCH_INFRASTRUCTURE,
                description=("Read-only: confirm live public bug-bounty boards publish reward ranges (no account, no submission)."),
                payer="N/A observational — sponsors fund published bounties",
                paid_event="Observational: live bounty pages with disclosed reward amounts",
                evidence_urls=bounty_urls
                or [
                    "https://immunefi.com/bug-bounty/",
                    "https://hackerone.com/bug-bounty-programs",
                ],
                evidence_notes="Public bounty directories disclose reward ranges.",
                required_agent_action="Fetch bounty boards; record programs + reward samples into ledger",
                acceptance_method="HTTP 200 + reward amount strings observed",
                time_to_feedback_hours=1,
                startup_cost_usd=Decimal("0"),
                expected_txn_value_usd=Decimal("50"),
                repeatability=0.6,
                automation_feasibility=0.95,
                ongoing_human_minutes_per_txn=0,
                one_time_bootstrap_minutes=0,
                setup_class=SetupClass.A_NONE,
                third_party_dependency="None (public web)",
                subjective_quality_risk=0.05,
                liability_reputation_risk=0.05,
                platform_account_risk=0.05,
                fulfillment_complexity=0.1,
                cheapest_validation="GET Immunefi/HackerOne public boards; record reward samples",
                blockers=[],
            )
        )

    # Deduplicate by mechanism_type keeping higher evidence count
    by_type: dict[str, MechanismSpec] = {}
    for m in specs:
        prev = by_type.get(m.mechanism_type)
        if prev is None or len(m.evidence_urls) > len(prev.evidence_urls):
            by_type[m.mechanism_type] = m
    # Attach leftover URLs as evidence enrichment
    _ = by_url
    return list(by_type.values())


def discover_mechanisms(research: ResearchPort | None = None) -> list[MechanismSpec]:
    research = research or PublicWebResearchAdapter()
    all_hits: list[ResearchHit] = []
    for q in MECHANISM_QUERIES:
        hits = research.search(q, limit=5)
        for h in hits:
            h.title = sanitize_untrusted(h.title)
            h.snippet = sanitize_untrusted(h.snippet)
        all_hits.extend(hits)

    # Also hit Challenge.gov HTML/API-ish public page directly
    try:
        import httpx

        client = httpx.Client(timeout=20, headers={"User-Agent": "ForageResearch/0.1"}, follow_redirects=True)
        resp = client.get("https://www.challenge.gov/")
        if resp.status_code == 200:
            all_hits.append(
                ResearchHit(
                    title="Challenge.gov prize competitions",
                    url="https://www.challenge.gov/",
                    snippet=sanitize_untrusted(resp.text[:1500]),
                    source="challenge_gov",
                )
            )
        # Immunefi public bounties page
        resp2 = client.get("https://immunefi.com/bug-bounty/")
        if resp2.status_code == 200 and ("bounty" in resp2.text.lower() or "reward" in resp2.text.lower()):
            all_hits.append(
                ResearchHit(
                    title="Immunefi bug bounty programs",
                    url="https://immunefi.com/bug-bounty/",
                    snippet="Public bug bounty board with published rewards",
                    source="immunefi",
                )
            )
        # HackerOne directory (public)
        resp3 = client.get("https://hackerone.com/bug-bounty-programs")
        if resp3.status_code == 200:
            all_hits.append(
                ResearchHit(
                    title="HackerOne bug bounty programs",
                    url="https://hackerone.com/bug-bounty-programs",
                    snippet="Public directory of bug bounty programs",
                    source="hackerone",
                )
            )
    except Exception:
        pass

    specs = _seed_mechanisms_from_hits(all_hits)
    # Ensure ≥10: if short, split evidence into additional documented mechanism variants from hits
    if len(specs) < 10:
        specs.extend(_pad_mechanisms_from_hits(all_hits, need=10 - len(specs)))
    scored: list[MechanismSpec] = []
    for m in specs:
        m = eligibility_check(m)
        m = score_mechanism(m)
        scored.append(m)
    scored.sort(key=lambda x: (x.eligible, x.score), reverse=True)
    return scored


def _pad_mechanisms_from_hits(hits: list[ResearchHit], need: int) -> list[MechanismSpec]:
    """Additional evidence-tied mechanisms when primary map is thin — still evidence-required."""
    extras: list[MechanismSpec] = []
    templates = [
        (
            "stackoverflow_bounty_market",
            "stack overflow",
            "Q&A bounties transfer reputation/visibility; cash rare but acceptance is explicit",
            "Bounty awarded to accepted answer",
        ),
        (
            "public_domain_republish_sku",
            "public domain",
            "Repackage public-domain corpora as fixed SKUs with clear provenance",
            "SKU purchase",
        ),
        (
            "keyword_watch_digest",
            "rss",
            "Sell narrow keyword digests compiled from public RSS/Atom",
            "Subscription payment",
        ),
        (
            "price_change_ping",
            "pricing",
            "Notify subscribers when a public price page changes (objective DOM/hash)",
            "Subscription payment",
        ),
        (
            "compliance_changelog_monitor",
            "monitor",
            "Watch public regulatory pages; sell change digests",
            "Subscription payment",
        ),
        (
            "oss_issue_cash_bounty",
            "issue",
            "Cash-for-issue programs on public repos with CI acceptance",
            "Bounty payout on merge",
        ),
    ]
    for mech_type, key, desc, event in templates:
        if len(extras) >= need:
            break
        matched = [h for h in hits if key in f"{h.title} {h.snippet}".lower() and h.url]
        if not matched and hits:
            matched = hits[:2]
        if not matched:
            continue
        extras.append(
            MechanismSpec(
                mechanism_type=mech_type,
                description=desc,
                payer="Buyer / bounty funder",
                paid_event=event,
                evidence_urls=[h.url for h in matched if h.url][:4],
                evidence_notes=sanitize_untrusted(matched[0].snippet[:240]),
                required_agent_action="Execute narrow automated workflow meeting published criteria",
                acceptance_method="Payment or platform acceptance event",
                time_to_feedback_hours=96,
                startup_cost_usd=Decimal("5"),
                expected_txn_value_usd=Decimal("25"),
                repeatability=0.7,
                automation_feasibility=0.7,
                ongoing_human_minutes_per_txn=2,
                one_time_bootstrap_minutes=30,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                third_party_dependency="Varies",
                subjective_quality_risk=0.35,
                liability_reputation_risk=0.3,
                platform_account_risk=0.4,
                fulfillment_complexity=0.45,
                cheapest_validation="Confirm live inventory + produce one sample output without bespoke client work",
                blockers=["possible_account"],
            )
        )
    return extras


def select_finalists(mechanisms: list[MechanismSpec], k: int = 3) -> list[MechanismSpec]:
    """Pick k capital-eligible EARNING mechanisms (not research infrastructure)."""
    eligible = [m for m in mechanisms if m.eligible and m.role == MechanismRole.EARNING_MECHANISM]
    eligible.sort(key=lambda m: m.score, reverse=True)
    selected: list[MechanismSpec] = []
    seen_families: set[str] = set()

    def family(m: MechanismSpec) -> str:
        parts = m.mechanism_type.split("_")
        return "_".join(parts[:2]) if len(parts) >= 2 else m.mechanism_type

    for m in eligible:
        if len(selected) >= k:
            break
        fam = family(m)
        if fam in seen_families:
            continue
        if m.mechanism_type.endswith("_inventory_signal"):
            continue
        selected.append(m)
        seen_families.add(fam)

    if len(selected) < k:
        for m in eligible:
            if m in selected or m.mechanism_type.endswith("_inventory_signal"):
                continue
            selected.append(m)
            if len(selected) >= k:
                break
    return selected[:k]


def ranking_table(mechanisms: list[MechanismSpec]) -> list[dict[str, Any]]:
    rows = []
    for m in mechanisms:
        rows.append(
            {
                "mechanism_id": m.mechanism_id,
                "type": m.mechanism_type,
                "eligible": m.eligible,
                "reject": m.reject_reasons,
                "score": m.score,
                "setup": m.setup_class.value,
                "ongoing_min": m.ongoing_human_minutes_per_txn,
                "bootstrap_min": m.one_time_bootstrap_minutes,
                "subj_risk": m.subjective_quality_risk,
                "components": m.score_components,
                "evidence": m.evidence_urls[:3],
            }
        )
    return rows


def mechanism_to_experiment_fields(m: MechanismSpec) -> dict[str, Any]:
    return {
        "customer": m.payer,
        "problem": m.description,
        "offer": m.required_agent_action,
        "price_usd": m.expected_txn_value_usd,
        "acquisition_channel": m.evidence_urls[0] if m.evidence_urls else m.mechanism_type,
        "fulfillment_method": m.acceptance_method,
        "budget_usd": min(m.startup_cost_usd * Decimal("2") + Decimal("5"), Decimal("20")),
        "evidence_target": m.cheapest_validation,
        "continue_condition": f"external paid_event observed: {m.paid_event}",
        "pause_condition": "waiting_external_or_bootstrap",
        "stop_condition": "falsified_or_budget_or_deadline",
        "mechanism_id": m.mechanism_id,
        "mechanism_type": m.mechanism_type,
        "setup_class": m.setup_class.value,
        "max_spend_usd": str(min(m.startup_cost_usd * Decimal("2") + Decimal("5"), Decimal("20"))),
        "time_to_feedback_hours": m.time_to_feedback_hours,
        "one_time_bootstrap_minutes": m.one_time_bootstrap_minutes,
        "ongoing_human_minutes_per_txn": m.ongoing_human_minutes_per_txn,
        "key_risk": max(
            [
                ("subjective", m.subjective_quality_risk),
                ("liability", m.liability_reputation_risk),
                ("platform", m.platform_account_risk),
            ],
            key=lambda x: x[1],
        )[0],
    }


@dataclass
class CheapTestResult:
    mechanism_id: str
    mechanism_type: str
    launched: bool
    status: str
    detail: dict[str, Any]
    needs_bootstrap: bool
    bootstrap_items: list[str]


def run_cheap_test(m: MechanismSpec) -> CheapTestResult:
    """Execute cheapest real test when setup allows.

    Abstract mechanism classes must not request owner KYC/setup. Bootstrap is
    gated on concrete PaidOpportunity cash-path (see opportunities.py).
    """
    # Setup A / research inventory: public probes only
    if m.setup_class == SetupClass.A_NONE or m.mechanism_type.endswith("_inventory_signal"):
        detail = _probe_public_inventory(m)
        if m.role == MechanismRole.RESEARCH_INFRASTRUCTURE:
            detail["role"] = "RESEARCH_INFRASTRUCTURE"
            detail["capital_eligible"] = False
        return CheapTestResult(
            mechanism_id=m.mechanism_id,
            mechanism_type=m.mechanism_type,
            launched=True,
            status="EXTERNAL_SIGNAL" if detail.get("ok") else "FAILED",
            detail=detail,
            needs_bootstrap=False,
            bootstrap_items=[],
        )

    # Earning mechanisms without owner setup yet: probe public evidence only.
    detail = _probe_public_inventory(m)
    detail["note"] = "partial_test_without_owner_bootstrap; no KYC requested until concrete opportunity clears cash-path gate"
    detail["setup_class"] = m.setup_class.value
    return CheapTestResult(
        mechanism_id=m.mechanism_id,
        mechanism_type=m.mechanism_type,
        launched=bool(detail.get("ok")),
        status="EXTERNAL_SIGNAL" if detail.get("ok") else "FAILED",
        detail=detail,
        needs_bootstrap=False,
        bootstrap_items=[],
    )


def _probe_public_inventory(m: MechanismSpec) -> dict[str, Any]:
    """Live public-web probe: confirm economic inventory still visible."""
    import httpx

    found: list[dict[str, str]] = []
    client = httpx.Client(timeout=20, headers={"User-Agent": "ForageResearch/0.1"}, follow_redirects=True)
    urls = list(m.evidence_urls[:5]) or []
    if m.mechanism_type.startswith("objective_security") or "bounty" in m.mechanism_type:
        urls = list(
            dict.fromkeys(
                urls
                + [
                    "https://immunefi.com/bug-bounty/",
                    "https://hackerone.com/bug-bounty-programs",
                ]
            )
        )
    if "challenge" in m.mechanism_type or "prize" in m.mechanism_type:
        urls = list(dict.fromkeys(urls + ["https://www.challenge.gov/"]))

    money_re = re.compile(r"(\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*\s?(?:USD|USDC|ETH))")
    for url in urls[:6]:
        try:
            resp = client.get(url)
            text = resp.text[:50000] if resp.status_code == 200 else ""
            amounts = money_re.findall(text)[:8]
            if resp.status_code == 200 and (amounts or "bounty" in text.lower() or "prize" in text.lower() or "challenge" in text.lower()):
                found.append(
                    {
                        "url": url,
                        "status": str(resp.status_code),
                        "amounts_sample": ", ".join(amounts[:5]),
                        "snippet": sanitize_untrusted(text[:200]),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            found.append({"url": url, "error": str(exc)})

    return {
        "ok": len([f for f in found if f.get("status") == "200"]) >= 1,
        "inventory": found,
        "paid_event_hypothesis": m.paid_event,
    }


def run_finalist_tests_parallel(finalists: list[MechanismSpec]) -> list[CheapTestResult]:
    results: list[CheapTestResult] = []
    with ThreadPoolExecutor(max_workers=min(3, len(finalists) or 1)) as pool:
        futs = {pool.submit(run_cheap_test, m): m for m in finalists}
        for fut in as_completed(futs):
            results.append(fut.result())
    return results


def consolidated_bootstrap_request(results: list[CheapTestResult], finalists: list[MechanismSpec]) -> str:
    """Abstract mechanism finalists must NOT request owner KYC/setup.

    Bootstrap is only justified after a concrete PaidOpportunity clears the
    cash-path gate (see forage.opportunities.justify_one_bootstrap).
    """
    _ = (results, finalists)
    return (
        "No owner bootstrap requested from mechanism-class ranking. "
        "RESEARCH_INFRASTRUCTURE inventory feeds cannot win capital allocation. "
        "Run `forage mechanisms hunt` for concrete live opportunities before any setup/KYC."
    )


def mechanisms_report_payload(
    all_m: list[MechanismSpec],
    finalists: list[MechanismSpec],
    results: list[CheapTestResult],
) -> dict[str, Any]:
    return {
        "considered": [
            {
                "id": m.mechanism_id,
                "type": m.mechanism_type,
                "evidence": m.evidence_urls[:4],
                "notes": m.evidence_notes[:200],
                "eligible": m.eligible,
                "score": m.score,
            }
            for m in all_m
        ],
        "ranking": ranking_table(all_m),
        "finalists": [json.loads(m.model_dump_json()) for m in finalists],
        "tests": [
            {
                "mechanism_id": r.mechanism_id,
                "type": r.mechanism_type,
                "launched": r.launched,
                "status": r.status,
                "needs_bootstrap": r.needs_bootstrap,
                "bootstrap_items": r.bootstrap_items,
                "detail": r.detail,
            }
            for r in results
        ],
        "bootstrap_request": consolidated_bootstrap_request(results, finalists),
    }
