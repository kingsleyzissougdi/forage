"""Concrete paid-opportunity hunt + cash-path gate + 0–5 ranking.

Research infrastructure (inventory feeds) is not capital-eligible.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx

from forage.adapters.gateway import sanitize_untrusted
from forage.models import PaidOpportunity, SetupClass

UA = {"User-Agent": "ForageResearch/0.1 (+local; read-only research)"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=25.0, headers=UA, follow_redirects=True)


def _money_to_decimal(raw: str) -> Decimal:
    s = raw.replace(",", "").replace(" ", "")
    mult = Decimal("1")
    if s.startswith("€"):
        s = s[1:]
        # treat EUR ≈ USD for ranking transparency (not FX precision)
    elif s.startswith("$"):
        s = s[1:]
    try:
        return Decimal(s) * mult
    except Exception:
        return Decimal("0")


def _parse_range(rewards: list[str]) -> tuple[Decimal, Decimal]:
    vals: list[Decimal] = []
    for r in rewards:
        parts = re.split(r"\s*[-–]\s*", r)
        for p in parts:
            p = p.strip()
            if re.match(r"^[€$]?\s?[\d]", p):
                vals.append(_money_to_decimal(p))
    vals = [v for v in vals if v > 0]
    if not vals:
        return Decimal("0"), Decimal("0")
    return min(vals), max(vals)


def _score_0_5(x: float, lo: float, hi: float, higher_better: bool = True) -> float:
    if hi <= lo:
        return 0.0
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if not higher_better:
        t = 1.0 - t
    return round(5.0 * t, 2)


def cash_path_complete(o: PaidOpportunity) -> list[str]:
    """Hard gate: missing any required field → cannot request bootstrap."""
    missing: list[str] = []
    if not o.platform:
        missing.append("platform")
    if not o.source_url:
        missing.append("source_url")
    if not o.title:
        missing.append("title")
    if o.payout_usd_high <= 0:
        missing.append("payout")
    if not o.acceptance_rule:
        missing.append("acceptance_rule")
    if not o.required_deliverable:
        missing.append("required_deliverable")
    if not o.currently_open:
        missing.append("currently_open")
    if o.agent_exec_hours <= 0:
        missing.append("agent_exec_hours")
    if o.autonomy_confidence <= 0:
        missing.append("autonomy_confidence")
    if not o.key_risk:
        missing.append("key_risk")
    return missing


def eligibility_opportunity(o: PaidOpportunity) -> PaidOpportunity:
    reasons: list[str] = []
    blob = f"{o.title} {o.required_deliverable} {o.acceptance_rule} {o.key_risk}".lower()
    if any(x in blob for x in ("fake review", "spam", "deceive", "impersonat")):
        reasons.append("prohibited_conduct")
    if any(x in blob for x in ("meeting", "zoom", "phone call", "consult", "demo day", "pitch")):
        reasons.append("synchronous_or_consulting")
    if any(x in blob for x in ("trade", "trading", "perps", "swap of at least")):
        reasons.append("autonomous_financial_trading")
    if any(x in blob for x in ("judging criteria", "winners are selected", "1st place", "prize pool")):
        reasons.append("subjective_contest_not_acceptance_payout")
    if any(x in blob for x in ("x post", "twitter", "tweet", "hype video", "memorable moment")):
        reasons.append("subjective_social_content")
    if o.autonomy_confidence < 0.25:
        reasons.append("autonomy_too_low")
    if o.ongoing_human_minutes >= 30:
        reasons.append("ongoing_human_heavy")
    gate = cash_path_complete(o)
    if gate:
        reasons.append("incomplete_cash_path:" + ",".join(gate))
    # Expected economics: mid payout vs cost
    mid = (o.payout_usd_low + o.payout_usd_high) / Decimal("2")
    if mid <= o.agent_exec_cost_usd:
        reasons.append("nonpositive_expected_net_at_midpoint")
    o.reject_reasons = reasons
    o.eligible = len(reasons) == 0
    return o


def score_opportunity(o: PaidOpportunity) -> PaidOpportunity:
    """Interpretable 0–5 components. No near-zero denominator explosions."""
    mid = float((o.payout_usd_low + o.payout_usd_high) / Decimal("2"))
    net = mid - float(o.agent_exec_cost_usd)
    setup_map = {
        SetupClass.A_NONE: 5.0,
        SetupClass.B_EXISTING: 4.0,
        SetupClass.C_AGENT_CREATABLE: 3.5,
        SetupClass.D_ONE_TIME_OWNER: 2.0,
        SetupClass.E_ONGOING_OWNER: 0.5,
    }
    scores = {
        "expected_net_profit": _score_0_5(net, 0, 500, True),
        "time_to_first_payment": _score_0_5(o.agent_exec_hours, 1, 80, False),
        "repeatability": _score_0_5(3.0 if "bounty" in o.mechanism_type else 2.0, 0, 5, True),
        "objective_verifiability": _score_0_5(
            4.5 if "triage" in o.acceptance_rule.lower() or "scope" in o.acceptance_rule.lower() else 3.0,
            0,
            5,
            True,
        ),
        "autonomy_confidence": round(5.0 * o.autonomy_confidence, 2),
        "low_setup_friction": setup_map.get(o.setup_class, 2.0),
        "low_ongoing_human": _score_0_5(o.ongoing_human_minutes, 0, 30, False),
        "low_exec_cost": _score_0_5(float(o.agent_exec_cost_usd), 0, 50, False),
    }
    # Primary ratio proxy without explosive multiplication:
    # mean(value-ish) - 0.25 * mean(burden-ish inverted already as "low_*")
    value = (
        scores["expected_net_profit"]
        + scores["time_to_first_payment"]
        + scores["objective_verifiability"]
        + scores["autonomy_confidence"]
        + scores["repeatability"]
    ) / 5.0
    burden_ok = (scores["low_setup_friction"] + scores["low_ongoing_human"] + scores["low_exec_cost"]) / 3.0
    o.scores = scores
    o.rank_score = round(0.65 * value + 0.35 * burden_ok, 3)
    return o


def _fetch_program(url: str, platform: str) -> PaidOpportunity | None:
    try:
        with _client() as client:
            resp = client.get(url)
            status = resp.status_code
            if status != 200:
                return None
            text = unescape(resp.text)
    except Exception:
        return None

    title_m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    title = re.sub("<[^>]+>", "", title_m.group(1)).strip() if title_m else url
    title = sanitize_untrusted(title)[:160]
    reward_strs = list(
        dict.fromkeys(
            re.findall(
                r"(?:€|\$)\s?[\d][\d,\.]*(?:\s*[-–]\s*(?:€|\$)?\s?[\d][\d,\.]*)?",
                text,
            )
        )
    )[:12]
    low, high = _parse_range(reward_strs)
    if high <= 0:
        return None

    open_hints = ("open", "public", "launch", "active", "in scope", "eligible")
    currently_open = any(h in text.lower() for h in open_hints) and status == 200

    # Heuristic autonomy: web/app bug bounties slightly higher than smart-contract criticals
    blob = text.lower()[:20000]
    if "smart contract" in blob and "critical" in blob:
        autonomy = 0.22
        exec_hours = 40.0
        deliverable = "Valid in-scope vulnerability report meeting program severity rules"
        risk = "Specialized security skill; false-positive / out-of-scope risk; reputational if careless"
        mechanism = "objective_security_bounty"
    elif "bot" in title.lower() or "bot bounty" in blob:
        autonomy = 0.40
        exec_hours = 12.0
        deliverable = "Demonstration of in-scope bot/bypass behavior per published program rules"
        risk = "May prohibit automation or require human research judgment"
        mechanism = "explicit_criteria_marketplace"
    else:
        autonomy = 0.30
        exec_hours = 20.0
        deliverable = "In-scope vulnerability report with repro steps accepted by triage"
        risk = "Competitive researcher market; KYC for payout; scope violations"
        mechanism = "objective_security_bounty"

    acceptance = "Program triage accepts a valid in-scope report at stated severity; payout per published reward table"
    mins = re.findall(r"(?:minimum|min)[^€$]{0,20}([€$]\s?[\d,]+)", text, re.I)
    if mins:
        low = max(low, _money_to_decimal(mins[0]))

    return PaidOpportunity(
        platform=platform,
        source_url=url,
        title=title,
        payout_usd_low=low,
        payout_usd_high=high,
        acceptance_rule=acceptance,
        required_deliverable=deliverable,
        currently_open=currently_open,
        evidence_snippet=sanitize_untrusted(f"rewards_seen={reward_strs[:5]}; open_hints={currently_open}"),
        agent_exec_cost_usd=Decimal("2"),
        agent_exec_hours=exec_hours,
        ongoing_human_minutes=5.0,
        setup_class=SetupClass.D_ONE_TIME_OWNER,
        setup_required=f"{platform} researcher account + payout KYC/tax identity",
        autonomy_confidence=autonomy,
        key_risk=risk,
        mechanism_type=mechanism,
    )


def _discover_program_urls() -> list[tuple[str, str]]:
    """Return (platform, url) candidates from public directories."""
    found: list[tuple[str, str]] = []
    with _client() as client:
        # YesWeHack
        try:
            r = client.get("https://yeswehack.com/programs")
            for path in dict.fromkeys(re.findall(r'href="(/programs/[^"]+)"', r.text)):
                if path.count("/") >= 2:
                    found.append(("YesWeHack", urljoin("https://yeswehack.com", path)))
        except Exception:
            pass
        # HackenProof
        try:
            r = client.get("https://hackenproof.com/programs")
            for slug in dict.fromkeys(re.findall(r"/programs/([a-z0-9][a-z0-9-]{2,})", r.text, re.I)):
                if slug in {"logo", "filter", "search"}:
                    continue
                found.append(("HackenProof", f"https://hackenproof.com/programs/{slug}"))
        except Exception:
            pass
        # Immunefi information pages
        try:
            r = client.get("https://immunefi.com/bug-bounty/")
            for path in dict.fromkeys(re.findall(r'href="(/bug-bounty/[^"/]+/information/)"', r.text)):
                found.append(("Immunefi", urljoin("https://immunefi.com", path)))
        except Exception:
            pass
    # Dedupe preserve order
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p, u in found:
        if u not in seen:
            seen.add(u)
            out.append((p, u))
    return out


def _hunt_superteam() -> list[PaidOpportunity]:
    """Concrete open Superteam Earn listings (explicit-criteria marketplace)."""
    opps: list[PaidOpportunity] = []
    try:
        with _client() as client:
            r = client.get("https://superteam.fun/api/listings")
            if r.status_code != 200:
                return []
            listings = r.json()
    except Exception:
        return []

    for item in listings if isinstance(listings, list) else []:
        if item.get("status") != "OPEN":
            continue
        reward = item.get("rewardAmount")
        if reward is None:
            continue
        try:
            amount = Decimal(str(reward))
        except Exception:
            continue
        if amount <= 0:
            continue
        slug = item.get("slug") or ""
        title = sanitize_untrusted(str(item.get("title") or slug))[:160]
        token = str(item.get("token") or "USD")
        agent_access = str(item.get("agentAccess") or "HUMAN_ONLY")
        url = f"https://superteam.fun/earn/listing/{slug}"
        deadline = str(item.get("deadline") or "")
        currently_open = True
        # Fetch detail when cheap enough for acceptance text
        acceptance = f"Sponsor selects submission(s) per published listing rules; stated reward {amount} {token}; deadline {deadline or 'n/a'}"
        deliverable = f"Complete deliverable described in listing '{title}' and submit via Superteam Earn"
        risk = "Contest/judging risk; wallet/account setup; social identity often required"
        autonomy = 0.20
        exec_hours = 8.0
        mechanism = "explicit_criteria_marketplace"
        if agent_access == "AGENT_ALLOWED":
            autonomy = 0.45
            exec_hours = 6.0
            risk = "Agent-allowed listing but may still require social identity / subjective selection"
        evidence_extra = f"api_status=OPEN;agentAccess={agent_access}"
        # Enrich detail pages only for agent-allowed listings (cost control).
        if agent_access == "AGENT_ALLOWED":
            try:
                with _client() as client:
                    page = client.get(url)
                if page.status_code == 200:
                    m = re.search(
                        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                        page.text,
                        re.S,
                    )
                    if m:
                        props = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
                        listing = props.get("listing") or {}
                        desc_html = listing.get("description") or ""
                        desc = re.sub("<[^>]+>", " ", desc_html)
                        desc = sanitize_untrusted(re.sub(r"\s+", " ", desc))[:1200]
                        if desc:
                            deliverable = desc[:400]
                            acceptance = f"Listing rules as published on Superteam Earn; reward {amount} {token}; status OPEN; deadline {deadline}"
                            evidence_extra = desc[:240]
                        blob = (title + " " + (desc or "")).lower()
                        if "trade" in blob or ("xp" in blob and "arena" in blob):
                            autonomy = min(autonomy, 0.15)
                            risk = "Requires trading / arena XP; outside Forage policy"
                        if "x post" in blob or "twitter" in blob:
                            autonomy = min(autonomy, 0.25)
                            risk = "Requires public social identity / subjective content judgment"
            except Exception:
                pass
        else:
            # HUMAN_ONLY / contest-style social listings: mark low autonomy without fetch.
            tlow = title.lower()
            if any(k in tlow for k in ("video", "x post", "twitter", "hype", "memorable", "pitch")):
                autonomy = 0.15
                risk = "HUMAN_ONLY subjective content / social contest"
            elif any(k in tlow for k in ("hackathon", "mvp", "demo day")):
                autonomy = 0.18
                risk = "HUMAN_ONLY event/hackathon; subjective selection"

        opps.append(
            PaidOpportunity(
                platform="Superteam Earn",
                source_url=url,
                title=title,
                payout_usd_low=amount,
                payout_usd_high=amount,
                acceptance_rule=acceptance,
                required_deliverable=deliverable[:500],
                currently_open=currently_open,
                evidence_snippet=sanitize_untrusted(evidence_extra),
                agent_exec_cost_usd=Decimal("3"),
                agent_exec_hours=exec_hours,
                ongoing_human_minutes=10.0,
                setup_class=SetupClass.D_ONE_TIME_OWNER,
                setup_required="Superteam Earn account + payout wallet; often X/social identity",
                autonomy_confidence=autonomy,
                key_risk=risk,
                mechanism_type=mechanism,
            )
        )
    return opps


def _probe_microtask_public_surface() -> list[PaidOpportunity]:
    """Microtask markets: record only if a concrete open task+payout is visible without login."""
    # Public marketing pages without login do not expose task IDs / exact HIT payouts.
    # Return empty — incomplete cash-path, not inventable.
    _ = None
    try:
        with _client() as client:
            r = client.get("https://www.clickworker.com/clickworker/")
            if r.status_code != 200:
                return []
            # No specific open HIT with exact payout is published on the marketing page.
            return []
    except Exception:
        return []


def hunt_concrete_opportunities(limit_fetch: int = 18) -> list[PaidOpportunity]:
    all_urls = _discover_program_urls()
    # Round-robin across platforms so one directory cannot dominate the fetch budget.
    by_plat: dict[str, list[tuple[str, str]]] = {}
    for p, u in all_urls:
        by_plat.setdefault(p, []).append((p, u))
    urls: list[tuple[str, str]] = []
    i = 0
    while len(urls) < limit_fetch:
        added = False
        for plat in by_plat:
            bucket = by_plat[plat]
            if i < len(bucket):
                urls.append(bucket[i])
                added = True
                if len(urls) >= limit_fetch:
                    break
        if not added:
            break
        i += 1
    opps: list[PaidOpportunity] = []

    def work(item: tuple[str, str]) -> PaidOpportunity | None:
        platform, url = item
        return _fetch_program(url, platform)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(work, u) for u in urls]
        futs.append(pool.submit(_hunt_superteam))
        futs.append(pool.submit(_probe_microtask_public_surface))
        for fut in as_completed(futs):
            try:
                result = fut.result()
            except Exception:
                result = None
            batch: list[PaidOpportunity]
            if result is None:
                continue
            if isinstance(result, list):
                batch = result
            else:
                batch = [result]
            for o in batch:
                o = eligibility_opportunity(o)
                o = score_opportunity(o)
                opps.append(o)

    opps.sort(
        key=lambda x: (x.eligible, x.rank_score, x.autonomy_confidence),
        reverse=True,
    )
    return opps


def top_opportunities(opps: list[PaidOpportunity], k: int = 3) -> list[PaidOpportunity]:
    """Rank concrete opportunities; prefer eligible, diversify platform."""
    ordered = sorted(
        opps,
        key=lambda x: (x.eligible, x.rank_score, x.autonomy_confidence),
        reverse=True,
    )
    selected: list[PaidOpportunity] = []
    seen_plat: set[str] = set()
    # Pass 1: eligible, diverse platforms
    for o in ordered:
        if not o.eligible:
            continue
        if o.platform in seen_plat:
            continue
        selected.append(o)
        seen_plat.add(o.platform)
        if len(selected) >= k:
            return selected
    # Pass 2: fill with highest-ranked remaining eligible
    for o in ordered:
        if not o.eligible or o in selected:
            continue
        selected.append(o)
        if len(selected) >= k:
            return selected
    # Pass 3: if still short, allow cash-path-complete ineligible (reportable; no bootstrap)
    for o in ordered:
        if o in selected:
            continue
        if cash_path_complete(o):
            continue
        selected.append(o)
        if len(selected) >= k:
            break
    return selected[:k]


def justify_one_bootstrap(top: list[PaidOpportunity]) -> str:
    """At most ONE bootstrap request — only if cash-path complete and plausible."""
    # Prefer any no-setup eligible opportunity: execute it instead of asking.
    for o in top:
        if o.eligible and o.setup_class == SetupClass.A_NONE:
            return f"No owner bootstrap requested. Execute no-setup opportunity first: {o.platform} — {o.title} ({o.source_url})."
    for o in top:
        if not o.eligible:
            continue
        if o.setup_class == SetupClass.A_NONE:
            continue  # nothing to ask
        # Require meaningful autonomy and positive mid net
        mid = (o.payout_usd_low + o.payout_usd_high) / Decimal("2")
        if o.autonomy_confidence < 0.45:
            continue
        if mid <= o.agent_exec_cost_usd:
            continue
        if cash_path_complete(o):
            continue
        return (
            "ONE bootstrap request (only blocker for a live cash-path opportunity):\n"
            f"- Platform: {o.platform}\n"
            f"- Opportunity: {o.title}\n"
            f"- URL: {o.source_url}\n"
            f"- Payout range: ${o.payout_usd_low}–${o.payout_usd_high}\n"
            f"- Acceptance: {o.acceptance_rule}\n"
            f"- Deliverable: {o.required_deliverable}\n"
            f"- Setup needed: {o.setup_required}\n"
            f"- Autonomy confidence: {o.autonomy_confidence}\n"
            f"- Rank score (0–5 blend): {o.rank_score}\n"
            "Do not open other accounts until this opportunity is attempted or rejected."
        )
    return (
        "No owner bootstrap requested. "
        "Concrete opportunities were found, but none clear cash-path + autonomy "
        "(Forage can plausibly fulfill) with setup as the only material blocker. "
        "Microtask boards do not expose specific open HIT/payout rows without login, "
        "so they cannot justify KYC yet."
    )


def opportunities_report(opps: list[PaidOpportunity], top: list[PaidOpportunity]) -> dict[str, Any]:
    return {
        "found": [json.loads(o.model_dump_json()) for o in opps],
        "top3": [json.loads(o.model_dump_json()) for o in top],
        "bootstrap_request": justify_one_bootstrap(top),
        "note": (
            "RESEARCH_INFRASTRUCTURE inventory feeds are excluded from capital allocation. "
            "Only EARNING opportunities with complete cash-path fields may request bootstrap."
        ),
    }
