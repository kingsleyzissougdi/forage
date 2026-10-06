"""Gen-0 bounded viability sprint: hard gate + expanded family hunt.

Does not redesign scoring. Caps inspections at max_inspect.
Excludes security / social / trading / consulting drift.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

from forage.adapters.gateway import sanitize_untrusted
from forage.models import PaidOpportunity, SetupClass
from forage.opportunities import (
    UA,
    _client,
    _hunt_superteam,
    _money_to_decimal,
    cash_path_complete,
    score_opportunity,
)

MAX_INSPECT_DEFAULT = 1000
MAX_HOURS_DEFAULT = 24.0


def hard_viability_gate(o: PaidOpportunity) -> list[str]:
    """ALL must pass for finalist status. Failures are logged, not ranked."""
    fails: list[str] = []
    if not o.currently_open:
        fails.append("not_live_now")
    if o.payout_usd_high <= 0:
        fails.append("payout_not_explicit")
    if not o.acceptance_rule or len(o.acceptance_rule.strip()) < 12:
        fails.append("acceptance_not_observable")
    if o.autonomy_confidence < 0.80:
        fails.append("autonomy_below_80pct")
    mid = (o.payout_usd_low + o.payout_usd_high) / Decimal("2")
    if mid <= o.agent_exec_cost_usd:
        fails.append("nonpositive_contribution_margin")
    if o.ongoing_human_minutes > 10:
        fails.append("ongoing_human_over_10_min")
    blob = f"{o.title} {o.required_deliverable} {o.acceptance_rule} {o.key_risk}".lower()
    if any(
        x in blob
        for x in (
            "fake review",
            "spam",
            "deceive",
            "impersonat",
            "wash trading",
        )
    ):
        fails.append("prohibited_or_deceptive")
    if any(x in blob for x in ("trade", "trading", "perps", "consult", "meeting", "zoom")):
        fails.append("high_liability_or_forbidden_family")
    if any(
        x in blob
        for x in (
            "x post",
            "twitter",
            "hype video",
            "judging criteria",
            "1st place",
            "prize pool",
        )
    ):
        fails.append("subjective_social_or_contest")
    # Security research requiring specialized human judgment — sprint exclusion
    if any(x in blob for x in ("vulnerability", "bug bounty", "in-scope report", "bot bounty")):
        fails.append("security_specialized_judgment")
    # Time to first external economic signal ≤ 7 days
    if o.agent_exec_hours > 7 * 24:
        fails.append("time_to_signal_over_7_days")
    missing = cash_path_complete(o)
    if missing:
        fails.append("incomplete_cash_path:" + ",".join(missing))
    return fails


def apply_viability(o: PaidOpportunity) -> PaidOpportunity:
    fails = hard_viability_gate(o)
    o.reject_reasons = fails
    o.eligible = len(fails) == 0
    return o


def _opp(
    *,
    platform: str,
    url: str,
    title: str,
    low: Decimal,
    high: Decimal,
    acceptance: str,
    deliverable: str,
    open_now: bool,
    autonomy: float,
    exec_hours: float,
    human_min: float,
    setup: SetupClass,
    setup_required: str,
    risk: str,
    mechanism: str,
    evidence: str = "",
    cost: Decimal = Decimal("2"),
) -> PaidOpportunity:
    return PaidOpportunity(
        platform=platform,
        source_url=url,
        title=sanitize_untrusted(title)[:160],
        payout_usd_low=low,
        payout_usd_high=high,
        acceptance_rule=acceptance,
        required_deliverable=sanitize_untrusted(deliverable)[:500],
        currently_open=open_now,
        evidence_snippet=sanitize_untrusted(evidence)[:400],
        agent_exec_cost_usd=cost,
        agent_exec_hours=exec_hours,
        ongoing_human_minutes=human_min,
        setup_class=setup,
        setup_required=setup_required,
        autonomy_confidence=max(0.0, min(1.0, autonomy)),
        key_risk=risk,
        mechanism_type=mechanism,
    )


def _hunt_github_oss_bounties(limit: int = 200) -> tuple[list[PaidOpportunity], int]:
    """Inspect GitHub issues labeled bounty / with $ in title."""
    opps: list[PaidOpportunity] = []
    inspected = 0
    queries = [
        "label:bounty state:open",
        "$ bounty in:title state:open",
        "algora bounty state:open",
        "issuehunt bounty state:open",
    ]
    with _client() as client:
        for q in queries:
            if inspected >= limit:
                break
            page = 1
            while inspected < limit and page <= 5:
                url = f"https://api.github.com/search/issues?q={quote_plus(q)}&per_page=50&page={page}&sort=updated"
                try:
                    r = client.get(url, headers={**UA, "Accept": "application/vnd.github+json"})
                    if r.status_code != 200:
                        break
                    items = r.json().get("items") or []
                except Exception:
                    break
                if not items:
                    break
                for it in items:
                    if inspected >= limit:
                        break
                    inspected += 1
                    title = it.get("title") or ""
                    body = (it.get("body") or "")[:2000]
                    html_url = it.get("html_url") or ""
                    blob = f"{title}\n{body}"
                    vals: list[Decimal] = []
                    for m in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", blob):
                        vals.append(_money_to_decimal(m))
                    for m in re.findall(r"\b(\d+)\s*USDC\b", blob, re.I):
                        vals.append(Decimal(m))
                    labels = [lb.get("name", "") for lb in (it.get("labels") or [])]
                    if not vals or max(vals) <= 0:
                        continue
                    vals = [v for v in vals if v < Decimal("100000")]
                    if not vals:
                        continue
                    low, high = min(vals), max(vals)
                    auton = 0.55
                    if any(k in blob.lower() for k in ("test", "ci", "pull request", "unit test")):
                        auton = 0.72
                    if any(k in blob.lower() for k in ("design", "ux", "community")):
                        auton = min(auton, 0.40)
                    if any(k in " ".join(labels).lower() for k in ("security", "audit")):
                        auton = min(auton, 0.35)
                    opps.append(
                        _opp(
                            platform="GitHub OSS bounty",
                            url=html_url,
                            title=title,
                            low=low,
                            high=high,
                            acceptance=("Maintainer merges qualifying PR / closes issue against stated bounty criteria (objective when CI/tests gate)"),
                            deliverable=("Implement issue scope; open PR; satisfy stated acceptance checks"),
                            open_now=(it.get("state") == "open"),
                            autonomy=auton,
                            exec_hours=16.0,
                            human_min=5.0,
                            setup=SetupClass.C_AGENT_CREATABLE,
                            setup_required=("GitHub identity; payout rail per bounty sponsor (varies)"),
                            risk=("Sponsor may not pay; scope creep; review subjectivity; account needed for PR"),
                            mechanism="oss_objective_pr_bounty",
                            evidence=(f"labels={labels[:6]}; money_seen={[str(v) for v in vals[:4]]}"),
                        )
                    )
                if len(items) < 50:
                    break
                page += 1
                time.sleep(0.35)
    return opps, inspected


def _hunt_drivendata(limit: int = 80) -> tuple[list[PaidOpportunity], int]:
    opps: list[PaidOpportunity] = []
    inspected = 0
    try:
        with _client() as client:
            r = client.get("https://www.drivendata.org/competitions/")
            if r.status_code != 200:
                return [], 0
            paths = list(dict.fromkeys(re.findall(r'href="(/competitions/\d+/[^"/]+/)"', r.text)))[:limit]
    except Exception:
        return [], 0

    def fetch_one(path: str) -> PaidOpportunity | None:
        nonlocal_url = f"https://www.drivendata.org{path}"
        try:
            with _client() as client:
                resp = client.get(nonlocal_url)
                if resp.status_code != 200:
                    return None
                body = resp.text
        except Exception:
            return None
        title_m = re.search(r"<title>(.*?)</title>", body, re.I | re.S)
        title = re.sub("<[^>]+>", "", title_m.group(1)).strip() if title_m else path
        prize_m = re.findall(r"((?:€|\$)\s?[\d,]+)\s*in prizes", body, re.I)
        money = re.findall(r"(?:€|\$)\s?[\d][\d,\.]*", body)
        vals: list[Decimal] = []
        for raw in prize_m or money[:6]:
            raw = re.sub(r"\s*in prizes", "", raw, flags=re.I)
            vals.append(_money_to_decimal(raw))
        vals = [v for v in vals if v > 0]
        if not vals:
            return None
        open_now = any(k in body.lower() for k in ("left", "active", "ongoing", "open"))
        return _opp(
            platform="DrivenData",
            url=nonlocal_url,
            title=title,
            low=min(vals),
            high=max(vals),
            acceptance="Machine-scored leaderboard / held-out evaluation per competition rules",
            deliverable="Train model; submit predictions; meet prize-eligibility rules",
            open_now=open_now,
            autonomy=0.70,
            exec_hours=80.0,
            human_min=5.0,
            setup=SetupClass.D_ONE_TIME_OWNER,
            setup_required="DrivenData account; sometimes KYC for prize payout",
            risk="Contest prize pool (not pay-per-task); long cycle; domain expertise",
            mechanism="machine_scored_challenge",
            evidence=sanitize_untrusted(f"prizes={prize_m[:3] or money[:3]}"),
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(fetch_one, p) for p in paths]
        for fut in as_completed(futs):
            inspected += 1
            try:
                o = fut.result()
            except Exception:
                o = None
            if o is not None:
                opps.append(o)
    return opps, inspected


def _hunt_kaggle_public(limit: int = 60) -> tuple[list[PaidOpportunity], int]:
    opps: list[PaidOpportunity] = []
    inspected = 0
    try:
        with _client() as client:
            r = client.get("https://www.kaggle.com/competitions")
            if r.status_code != 200:
                return [], 0
            text = r.text
    except Exception:
        return [], 0
    money_near = re.findall(
        r'"title"\s*:\s*"([^"]{5,120})"[^}]{0,400}"rewardDisplay"\s*:\s*"(\$[^"]+)"',
        text,
    )
    if not money_near:
        for m in re.finditer(r'href="(/competitions/[^"]+)"', text):
            if inspected >= limit:
                break
            chunk = text[max(0, m.start() - 80) : min(len(text), m.end() + 400)]
            rewards = re.findall(r"\$[\d,]+", chunk)
            inspected += 1
            if not rewards:
                continue
            vals = [_money_to_decimal(x) for x in rewards]
            vals = [v for v in vals if v > 0]
            if not vals:
                continue
            slug = m.group(1)
            opps.append(
                _opp(
                    platform="Kaggle",
                    url=f"https://www.kaggle.com{slug}",
                    title=slug.split("/")[-1].replace("-", " "),
                    low=min(vals),
                    high=max(vals),
                    acceptance="Kaggle leaderboard / metric evaluation on held-out test set",
                    deliverable="Submit scored predictions or notebooks per competition rules",
                    open_now=True,
                    autonomy=0.68,
                    exec_hours=100.0,
                    human_min=5.0,
                    setup=SetupClass.D_ONE_TIME_OWNER,
                    setup_required="Kaggle account; phone verify often; prize tax forms",
                    risk="Winner-take prize contest; long cycle; competitive ML skill",
                    mechanism="machine_scored_challenge",
                    evidence=f"rewards={rewards[:4]}",
                )
            )
        return opps, inspected

    for title, reward in money_near[:limit]:
        inspected += 1
        vals = [_money_to_decimal(x) for x in re.findall(r"\$[\d,]+", reward)]
        vals = [v for v in vals if v > 0]
        if not vals:
            continue
        opps.append(
            _opp(
                platform="Kaggle",
                url="https://www.kaggle.com/competitions",
                title=title,
                low=min(vals),
                high=max(vals),
                acceptance="Kaggle leaderboard / metric evaluation on held-out test set",
                deliverable="Submit scored predictions per competition rules",
                open_now=True,
                autonomy=0.68,
                exec_hours=100.0,
                human_min=5.0,
                setup=SetupClass.D_ONE_TIME_OWNER,
                setup_required="Kaggle account; prize tax forms",
                risk="Winner-take prize contest; long cycle",
                mechanism="machine_scored_challenge",
                evidence=f"rewardDisplay={reward}",
            )
        )
    return opps, inspected


def _hunt_issuehunt_public(limit: int = 100) -> tuple[list[PaidOpportunity], int]:
    opps: list[PaidOpportunity] = []
    inspected = 0
    try:
        with _client() as client:
            r = client.get("https://issuehunt.io/issues")
            if r.status_code != 200:
                return [], 0
            text = r.text
    except Exception:
        return [], 0
    for m in re.finditer(r'href="(https://issuehunt\.io/r/[^"]+|/r/[^"]+)"', text):
        if inspected >= limit:
            break
        inspected += 1
        href = m.group(1)
        url = href if href.startswith("http") else f"https://issuehunt.io{href}"
        chunk = text[max(0, m.start() - 100) : min(len(text), m.end() + 500)]
        rewards = re.findall(r"\$\s?[\d,]+", chunk)
        if not rewards:
            continue
        vals = [_money_to_decimal(x) for x in rewards]
        vals = [v for v in vals if 0 < v < Decimal("100000")]
        if not vals:
            continue
        title_m = re.search(r">([^<]{8,120})<", chunk)
        title = title_m.group(1).strip() if title_m else url
        opps.append(
            _opp(
                platform="IssueHunt",
                url=url,
                title=title,
                low=min(vals),
                high=max(vals),
                acceptance=("Repository maintainer accepts PR resolving funded issue; bounty claimed via IssueHunt"),
                deliverable="Code change resolving the funded GitHub issue",
                open_now=True,
                autonomy=0.65,
                exec_hours=20.0,
                human_min=5.0,
                setup=SetupClass.D_ONE_TIME_OWNER,
                setup_required="IssueHunt + GitHub account; payout setup",
                risk="Maintainer acceptance subjectivity; payout delays",
                mechanism="oss_objective_pr_bounty",
                evidence=f"rewards={rewards[:4]}",
            )
        )
    return opps, inspected


def _hunt_affiliate_public_pages() -> tuple[list[PaidOpportunity], int]:
    pages = [
        ("Amazon Associates", "https://affiliate-program.amazon.com/", "affiliate_referral"),
        ("Shopify affiliate", "https://www.shopify.com/affiliates", "affiliate_referral"),
        (
            "DigitalOcean referral",
            "https://www.digitalocean.com/open-source/credits/referral",
            "affiliate_referral",
        ),
    ]
    opps: list[PaidOpportunity] = []
    inspected = 0
    for name, url, mech in pages:
        inspected += 1
        try:
            with _client() as client:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                text = r.text
        except Exception:
            continue
        rewards = re.findall(
            r"(?:\$\s?[\d,]+(?:\.\d+)?%?|\d+%\s*(?:commission|CPA|revshare))",
            text,
            re.I,
        )[:8]
        if not rewards:
            continue
        opps.append(
            _opp(
                platform=name,
                url=url,
                title=f"{name} published affiliate program (marketing page)",
                low=Decimal("0"),
                high=Decimal("0"),
                acceptance="Conversion event per program terms (click→qualified purchase)",
                deliverable="Drive attributed referral conversions via disclosed tracking links",
                open_now=True,
                autonomy=0.50,
                exec_hours=24.0,
                human_min=5.0,
                setup=SetupClass.D_ONE_TIME_OWNER,
                setup_required="Affiliate account + tax identity; tracked assets",
                risk="No concrete live offer/CPA row without account; attribution fraud risk",
                mechanism=mech,
                evidence=f"rates_seen={rewards[:5]}",
                cost=Decimal("5"),
            )
        )
    return opps, inspected


def _hunt_monitoring_lead_pages() -> tuple[list[PaidOpportunity], int]:
    pages = [
        ("BuiltWith API pricing", "https://builtwith.com/api", "lead_referral"),
        ("Hunter.io pricing", "https://hunter.io/pricing", "lead_referral"),
    ]
    opps: list[PaidOpportunity] = []
    inspected = 0
    for name, url, mech in pages:
        inspected += 1
        try:
            with _client() as client:
                r = client.get(url)
                text = r.text if r.status_code == 200 else ""
        except Exception:
            text = ""
        rewards = re.findall(r"\$\s?[\d,]+(?:\.\d+)?", text)[:6]
        opps.append(
            _opp(
                platform=name,
                url=url,
                title=f"{name} public pricing page",
                low=Decimal("0"),
                high=Decimal("0"),
                acceptance="N/A — buy-side pricing, not a payer→agent bounty",
                deliverable="N/A",
                open_now=False,
                autonomy=0.0,
                exec_hours=1.0,
                human_min=0.0,
                setup=SetupClass.A_NONE,
                setup_required="none",
                risk="Not an earn-side opportunity; inspected for completeness",
                mechanism=mech,
                evidence=f"prices_seen={rewards[:4]}",
            )
        )
    return opps, inspected


def run_viability_sprint(
    *,
    max_inspect: int = MAX_INSPECT_DEFAULT,
    max_hours: float = MAX_HOURS_DEFAULT,
) -> dict[str, Any]:
    """Bounded sprint. Stops at max_inspect or max_hours."""
    t0 = time.time()
    inspected = 0
    opps: list[PaidOpportunity] = []

    def remaining() -> int:
        return max(0, max_inspect - inspected)

    def time_up() -> bool:
        return (time.time() - t0) / 3600.0 >= max_hours

    if remaining() and not time_up():
        try:
            st = _hunt_superteam()
            take = st[: remaining()]
            inspected += len(st)
            opps.extend(take)
        except Exception:
            pass

    hunters = [
        ("github_oss", lambda: _hunt_github_oss_bounties(limit=min(400, max(1, remaining())))),
        ("drivendata", lambda: _hunt_drivendata(limit=min(80, max(1, remaining())))),
        ("kaggle", lambda: _hunt_kaggle_public(limit=min(80, max(1, remaining())))),
        ("issuehunt", lambda: _hunt_issuehunt_public(limit=min(100, max(1, remaining())))),
        ("affiliate", lambda: _hunt_affiliate_public_pages()),
        ("monitoring_lead", lambda: _hunt_monitoring_lead_pages()),
    ]

    for _name, fn in hunters:
        if remaining() <= 0 or time_up():
            break
        try:
            batch, n = fn()
        except Exception:
            batch, n = [], 0
        inspected += n
        opps.extend(batch)

    viable: list[PaidOpportunity] = []
    scored: list[PaidOpportunity] = []
    for o in opps:
        o = apply_viability(o)
        o = score_opportunity(o)
        scored.append(o)
        if o.eligible:
            viable.append(o)

    viable.sort(key=lambda x: (x.rank_score, x.autonomy_confidence), reverse=True)
    top3 = viable[:3]

    elapsed_h = (time.time() - t0) / 3600.0
    finding = None
    bootstrap = "No owner bootstrap requested."
    relax = None
    if viable:
        for o in top3:
            if o.setup_class in (SetupClass.A_NONE, SetupClass.C_AGENT_CREATABLE):
                bootstrap = f"No owner bootstrap requested. Prefer executing lowest-setup viable opp first: {o.platform} — {o.title}"
                break
            if o.setup_class == SetupClass.D_ONE_TIME_OWNER:
                bootstrap = (
                    "ONE bootstrap request (setup is final blocker for a gate-passing opportunity):\n"
                    f"- Platform: {o.platform}\n"
                    f"- Opportunity: {o.title}\n"
                    f"- URL: {o.source_url}\n"
                    f"- Payout: ${o.payout_usd_low}–${o.payout_usd_high}\n"
                    f"- Setup: {o.setup_required}\n"
                    f"- Autonomy: {o.autonomy_confidence}\n"
                )
                break
    else:
        finding = "ZERO_SETUP_OBJECTIVE_TASK_MARKET = SPARSE"
        relax = (
            "RELAX_ONE: allow one-time KYC/account/payment setup "
            "(preferred order #1) so microtask HIT inventories and "
            "authenticated bounty boards can be inspected for objective rows "
            "that are invisible without login."
        )
        bootstrap = "No owner bootstrap requested."

    fail_counts: dict[str, int] = {}
    for o in scored:
        for r in o.reject_reasons:
            key = r.split(":")[0]
            fail_counts[key] = fail_counts.get(key, 0) + 1

    return {
        "inspected": inspected,
        "opportunities_materialized": len(scored),
        "viable_count": len(viable),
        "elapsed_hours": round(elapsed_h, 4),
        "stopped_reason": ("max_inspect" if inspected >= max_inspect else ("max_hours" if time_up() else "sources_exhausted")),
        "finding": finding,
        "relax_one_constraint": relax,
        "bootstrap_request": bootstrap,
        "fail_counts": fail_counts,
        "top3": [json.loads(o.model_dump_json()) for o in top3],
        "sample_highest_autonomy_rejected": [
            {
                "platform": o.platform,
                "title": o.title[:100],
                "url": o.source_url,
                "payout_high": str(o.payout_usd_high),
                "autonomy": o.autonomy_confidence,
                "reject": o.reject_reasons[:4],
            }
            for o in sorted(scored, key=lambda x: x.autonomy_confidence, reverse=True)[:12]
        ],
    }
