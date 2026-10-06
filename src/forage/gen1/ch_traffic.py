"""Post-hoc ACCESS classification for the CH market clock.

Does not change price, verdict, or SKU. SELF_TEST / listed_at semantics unchanged.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import parse_qs

BAZAAR_EXAMPLE_NUMBER = "00445790"
COMPANY_NUMBER_RE = re.compile(r"^[A-Z0-9]{6,8}$", re.I)

# Marketplace/index/liveness probes that fetch 402 and never intend to pay.
INDEXER_UA_TOKENS = (
    "healthcheck",
    "health-survey",
    "health probe",
    "healthprobe",
    "liveness",
    "census",
    "observer",
    "observatory",
    "prober",
    "probe/",
    "probe ",
    "-probe",
    "bazaar",
    "indexer",
    "index-liveness",
    "catalog",
    "catalogue",
    "conformance",
    "trust-oracle",
    "trustindex",
    "trust-scan",
    "uptime",
    "qos monitor",
    "never pays",
    "never settles",
    "no payment",
    "unpaid liveness",
    "unserved-demand",
    "allow402",
    "x402watch",
    "x402-doctor",
    "x402-collector",
    "x402stats",
    "x402-census",
    "x402-observer",
    "x402-health",
    "x402-reliability",
    "carbonmonitor",
    "402explorer",
    "crawler",
    "spider",
    "zerobot",
    "keptvowbot",
    "brickbluebot",
    "quality-review-bot",
    "bot;",
    "bot)",
    "bot/",
)

GENERIC_CRAWLER_TOKENS = ("bot", "crawler", "spider", "slurp", "bingpreview", "facebookexternalhit")


def parse_query(query: str | None) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(query or "", keep_blank_values=True).items() if v}


def ua_is_indexer(ua: str) -> bool:
    u = (ua or "").lower()
    return any(tok in u for tok in INDEXER_UA_TOKENS)


def ua_is_generic_crawler(ua: str) -> bool:
    u = (ua or "").lower()
    return any(tok in u for tok in GENERIC_CRAWLER_TOKENS)


def is_plausible_company_number(number: str) -> bool:
    n = (number or "").strip().upper()
    return bool(n) and bool(COMPANY_NUMBER_RE.match(n))


def classify_access(event: dict[str, Any]) -> dict[str, Any]:
    """Return tags for one ACCESS event. Exclusive `bucket` for counting."""
    path = str(event.get("path") or "")
    paid_route = path.startswith("/v1/uk_company_counterparty_preflight")
    params = parse_query(event.get("query"))
    number = (params.get("company_number") or "").strip()
    name = (params.get("company_name") or "").strip()
    ua = event.get("user_agent") or ""
    indexer = ua_is_indexer(ua) or ua_is_generic_crawler(ua)
    empty_query = paid_route and not params
    schema_example = number == BAZAAR_EXAMPLE_NUMBER
    plausible = bool(name) or (is_plausible_company_number(number) and not schema_example)

    if not paid_route:
        bucket = "non_sku_path"
    elif plausible:
        bucket = "plausible_lookup"
    elif schema_example:
        bucket = "schema_example"
    elif empty_query:
        bucket = "empty_query"
    else:
        bucket = "other_query"

    return {
        "paid_route": paid_route,
        "indexer_ua": indexer,
        "empty_query": empty_query,
        "schema_example": schema_example,
        "plausible_lookup": plausible,
        "company_number": number or None,
        "company_name": name or None,
        "bucket": bucket,
    }


def summarize_access(events: list[dict[str, Any]]) -> dict[str, Any]:
    paid = [e for e in events if str(e.get("path") or "").startswith("/v1/uk_company_counterparty_preflight")]
    unpaid = [e for e in paid if e.get("unpaid_402") or e.get("status") == 402]
    classified = [(e, classify_access(e)) for e in unpaid]
    buckets = Counter(c["bucket"] for _, c in classified)
    indexer_402 = sum(1 for _, c in classified if c["indexer_ua"])
    non_indexer = [(e, c) for e, c in classified if not c["indexer_ua"]]
    non_indexer_ips = sorted({e.get("client_ip") for e, _ in non_indexer if e.get("client_ip")})
    ip_hits: dict[str, int] = defaultdict(int)
    for e, c in non_indexer:
        ip = e.get("client_ip")
        if ip:
            ip_hits[ip] += 1
    repeat_ips = sorted(ip for ip, n in ip_hits.items() if n >= 2)
    plausible_events = [(e, c) for e, c in classified if c["plausible_lookup"]]
    plausible_numbers = sorted({c["company_number"] for _, c in plausible_events if c.get("company_number")})
    # Retry: same IP hitting paid route more than once (any unpaid)
    all_ip_hits: dict[str, int] = defaultdict(int)
    for e in unpaid:
        ip = e.get("client_ip")
        if ip:
            all_ip_hits[ip] += 1

    return {
        "unpaid_402": len(unpaid),
        "indexer_ua_402": indexer_402,
        "non_indexer_ua_402": len(non_indexer),
        "unique_non_indexer_ips": len(non_indexer_ips),
        "repeat_non_indexer_ips": len(repeat_ips),
        "repeat_any_ip": sum(1 for n in all_ip_hits.values() if n >= 2),
        "empty_query_402": buckets.get("empty_query", 0),
        "schema_example_402": buckets.get("schema_example", 0),
        "plausible_lookup_402": buckets.get("plausible_lookup", 0),
        "other_query_402": buckets.get("other_query", 0),
        "plausible_company_numbers": plausible_numbers,
        "exclusive_buckets": dict(buckets),
        "traffic_read": (
            "mostly_indexer_empty_or_example"
            if (indexer_402 + buckets.get("empty_query", 0) + buckets.get("schema_example", 0)) >= 0.8 * max(len(unpaid), 1)
            and buckets.get("plausible_lookup", 0) == 0
            else "mixed"
            if buckets.get("plausible_lookup", 0) == 0
            else "has_plausible_lookups"
        ),
    }
