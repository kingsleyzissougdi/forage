"""FOLLOW_THE_MONEY_V1: demand evidence ladder + machine-economy demand map.

Reconnaissance only — no generalized analytics platform, no Shopify bootstrap.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, field_validator

from forage.adapters.gateway import sanitize_untrusted
from forage.models import AgentEconomicRole, SetupClass

UA = {"User-Agent": "ForageResearch/0.1 (+local; follow-the-money)"}

USDC_DECIMALS = Decimal("1000000")  # 6 decimals


class DemandEvidenceLevel(str, Enum):
    """Product-category demand ladder (not protocol existence)."""

    E0 = "E0"  # plausible idea only
    E1 = "E1"  # comparable product/service exists
    E2 = "E2"  # comparable product has observable pricing
    E3 = "E3"  # observable real buyer/transaction activity
    E4 = "E4"  # repeat buyers / meaningful volume history
    E5 = "E5"  # Forage receives its own external demand event
    E6 = "E6"  # Forage receives verified revenue


LEVEL_RANK = {e: i for i, e in enumerate(DemandEvidenceLevel)}


class PaidServiceObservation(BaseModel):
    """One observed paid service / seller origin with activity metrics."""

    observation_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    source: str  # x402scan | cdp_discovery | swarms
    product_or_origin: str
    category: str
    price_usd: Decimal | None = None
    seller: str = ""
    unique_buyers: int | None = None
    tx_count: int | None = None
    volume_usd: Decimal | None = None
    latest_activity: str = ""
    chains: list[str] = Field(default_factory=list)
    demand_shape: str = "unknown"  # broad | concentrated_hf | possible_wash | unknown
    evidence_notes: str = ""
    evidence_urls: list[str] = Field(default_factory=list)

    @field_validator("price_usd", "volume_usd", mode="before")
    @classmethod
    def _dec(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return Decimal(str(v))


class DemandCategory(BaseModel):
    name: str
    evidence_level: DemandEvidenceLevel
    sellers_observed: int = 0
    buyers_observed: int = 0
    tx_activity: int = 0
    volume_usd: Decimal = Decimal("0")
    typical_price_usd: list[Decimal] = Field(default_factory=list)
    buyer_concentration_note: str = ""
    notable_products: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("volume_usd", mode="before")
    @classmethod
    def _dec(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class AdjacentOrganism(BaseModel):
    name: str
    parent_category: str
    evidence_level: DemandEvidenceLevel
    agent_role: AgentEconomicRole
    summary: str
    input_desc: str
    value_creation: str
    buyer_distribution: str
    transaction: str
    fulfillment: str
    verified_payment: str
    observed_comparable_pricing: str
    estimated_marginal_cost_usd: Decimal
    proposed_price_usd: Decimal
    expected_margin_usd: Decimal = Decimal("0")
    cheapest_test: str
    afternoon_budget_usd: Decimal = Decimal("20")
    time_to_external_signal_hours: float = 24
    setup_required: str = ""
    setup_class: SetupClass = SetupClass.D_ONE_TIME_OWNER
    ongoing_human_minutes: float = 0
    key_risk: str = ""
    evidence_urls: list[str] = Field(default_factory=list)

    @field_validator(
        "estimated_marginal_cost_usd",
        "proposed_price_usd",
        "expected_margin_usd",
        "afternoon_budget_usd",
        mode="before",
    )
    @classmethod
    def _dec(cls, v: Any) -> Decimal:
        return Decimal(str(v))


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers=UA, follow_redirects=True)


def classify_category(text: str) -> str:
    b = text.lower()
    if any(
        k in b
        for k in (
            "rpc",
            "eth_call",
            "balance",
            "blockchain",
            "token",
            "nft",
            "solana",
            "defi",
            "blockrun",
            "onesource",
            "chain",
        )
    ):
        return "blockchain_rpc_data"
    if any(k in b for k in ("search", "retrieval", "crawl", "exa", "web page", "fetch url", "serp")):
        return "retrieval_search"
    if any(k in b for k in ("llm", "inference", "gpt", "completion", "embedding", "venice", "model")):
        return "inference"
    if any(
        k in b
        for k in (
            "enrich",
            "email",
            "company",
            "person",
            "lead",
            "nansen",
            "stableenrich",
            "contents",
        )
    ):
        return "enrichment"
    if any(k in b for k in ("transform", "convert", "parse", "extract", "summar", "timezone")):
        return "transformation"
    if any(k in b for k in ("monitor", "alert", "watch", "change detect", "diff", "visualping")):
        return "monitoring"
    if any(k in b for k in ("identity", "reputation", "kyc", "wallet score")):
        return "identity_reputation"
    if any(k in b for k in ("pay", "payment", "settle", "bitrefill", "gift card", "voucher")):
        return "payments_transactions"
    if any(k in b for k in ("video", "media", "image")):
        return "media"
    return "other"


def demand_shape(unique_buyers: int | None, tx_count: int | None, volume_usd: Decimal | None) -> str:
    if unique_buyers is None or tx_count is None:
        return "unknown"
    if unique_buyers <= 0:
        return "unknown"
    tpb = tx_count / max(unique_buyers, 1)
    vol = float(volume_usd or 0)
    # Extremely high txs / few buyers → automated HF or possible self-test
    if unique_buyers < 100 and tpb > 1000:
        return "concentrated_hf"
    if unique_buyers < 30 and tpb > 100 and vol > 1000:
        return "possible_wash_or_hf"
    if unique_buyers >= 100:
        return "broad"
    if unique_buyers >= 30:
        return "moderate"
    return "narrow"


def scrape_x402scan() -> tuple[list[PaidServiceObservation], dict[str, Any]]:
    """Parse public x402scan HTML for seller activity + overall stats."""
    meta: dict[str, Any] = {"source": "https://www.x402scan.com/"}
    obs: list[PaidServiceObservation] = []
    try:
        with _client() as client:
            r = client.get("https://www.x402scan.com/")
            if r.status_code != 200:
                meta["error"] = f"http_{r.status_code}"
                return [], meta
            text = r.text
    except Exception as exc:  # noqa: BLE001
        meta["error"] = str(exc)
        return [], meta

    # Overall ecosystem stats (escaped in RSC payload)
    m = re.search(
        r'transactions\\":(\d+),\\"total_amount\\":(\d+),\\"unique_buyers\\":(\d+),\\"unique_sellers\\":(\d+)',
        text,
    )
    if m:
        meta["overall"] = {
            "transactions": int(m.group(1)),
            "volume_usd": float(Decimal(m.group(2)) / USDC_DECIMALS),
            "unique_buyers": int(m.group(3)),
            "unique_sellers": int(m.group(4)),
        }

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", text, re.S)
    big = max(scripts, key=len) if scripts else ""
    s = big.replace('\\"', '"').replace("\\/", "/")
    pat = re.compile(
        r'\{"recipients":\[([^\]]*)\],"origins":\[(.*?)\],"facilitators":\[(.*?)\],'
        r'"tx_count":(\d+),"total_amount":(\d+),"latest_block_timestamp":"([^"]+)",'
        r'"unique_buyers":(\d+),"chains":\[([^\]]*)\]\}',
        re.S,
    )
    for match in pat.finditer(s):
        origins = re.findall(r'"origin":"([^"]+)"', match.group(2))
        origin = origins[0] if origins else "unknown"
        tx = int(match.group(4))
        amt = Decimal(match.group(5)) / USDC_DECIMALS
        buyers = int(match.group(7))
        chains = re.findall(r'"([^"]+)"', match.group(8))
        cat = classify_category(origin + " " + " ".join(origins))
        shape = demand_shape(buyers, tx, amt)
        facs = re.findall(r'"([^"]+)"', match.group(3))[:4]
        obs.append(
            PaidServiceObservation(
                source="x402scan",
                product_or_origin=origin,
                category=cat,
                seller=origin,
                unique_buyers=buyers,
                tx_count=tx,
                volume_usd=amt,
                latest_activity=match.group(6),
                chains=chains,
                demand_shape=shape,
                evidence_notes=sanitize_untrusted(f"origins={origins[:4]}; facilitators={facs}"),
                evidence_urls=["https://www.x402scan.com/", origin if origin.startswith("http") else ""],
            )
        )
    meta["sellers_parsed"] = len(obs)
    return obs, meta


def scrape_cdp_discovery(limit: int = 200) -> tuple[list[PaidServiceObservation], dict[str, Any]]:
    """Coinbase CDP x402 discovery catalog — prices + descriptions (E2 catalog)."""
    meta: dict[str, Any] = {"source": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"}
    obs: list[PaidServiceObservation] = []
    try:
        with _client() as client:
            r = client.get(
                "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
                params={"pageSize": min(limit, 100)},
            )
            if r.status_code != 200:
                meta["error"] = f"http_{r.status_code}"
                return [], meta
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        meta["error"] = str(exc)
        return [], meta

    items = data.get("items") or []
    meta["pagination"] = data.get("pagination")
    meta["catalog_total"] = (data.get("pagination") or {}).get("total")
    for it in items[:limit]:
        desc = it.get("description") or ""
        resource = str(it.get("resource") or "")
        accepts = it.get("accepts") or []
        price = None
        if accepts:
            try:
                price = Decimal(str(accepts[0].get("amount") or "0")) / USDC_DECIMALS
            except Exception:
                price = None
        pay_to = ""
        if accepts:
            pay_to = str(accepts[0].get("payTo") or accepts[0].get("recipient") or "")
        cat = classify_category(f"{desc} {resource}")
        obs.append(
            PaidServiceObservation(
                source="cdp_discovery",
                product_or_origin=sanitize_untrusted(desc)[:160] or resource[:160],
                category=cat,
                price_usd=price,
                seller=pay_to,
                demand_shape="unknown",  # catalog listing ≠ observed spend
                evidence_notes="CDP bazaar listing with price; not itself E3 transaction proof",
                evidence_urls=[
                    "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
                    resource[:200],
                ],
            )
        )
    return obs, meta


def scrape_swarms_marketplace() -> tuple[list[PaidServiceObservation], dict[str, Any]]:
    meta: dict[str, Any] = {"source": "https://www.swarms.ai/marketplace"}
    obs: list[PaidServiceObservation] = []
    try:
        with _client() as client:
            r = client.get("https://www.swarms.ai/marketplace")
            if r.status_code != 200:
                meta["error"] = f"http_{r.status_code}"
                return [], meta
            text = r.text
    except Exception as exc:  # noqa: BLE001
        meta["error"] = str(exc)
        return [], meta

    money = re.findall(r"\$[\d,]+(?:\.\d+)?", text)[:30]
    sales_mentions = len(re.findall(r"\b(?:sold|sales|purchase)", text, re.I))
    meta["price_tokens_seen"] = money[:15]
    meta["sales_word_hits"] = sales_mentions
    # Marketing page — no per-listing tx counts observed → E1/E2 at best for marketplace mechanism
    obs.append(
        PaidServiceObservation(
            source="swarms",
            product_or_origin="Swarms marketplace (aggregate marketing page)",
            category="other",
            demand_shape="unknown",
            evidence_notes=(
                f"Public marketplace page reachable; price tokens={money[:8]}; sales_word_hits={sales_mentions}. No per-listing buyer/tx volume extracted."
            ),
            evidence_urls=["https://www.swarms.ai/marketplace"],
        )
    )
    return obs, meta


def build_demand_map(
    x402_obs: list[PaidServiceObservation],
    cdp_obs: list[PaidServiceObservation],
) -> list[DemandCategory]:
    """Cluster observed spend (prefer x402scan activity) + enrich with CDP pricing."""
    by_cat: dict[str, list[PaidServiceObservation]] = defaultdict(list)
    for o in x402_obs:
        by_cat[o.category].append(o)

    cdp_prices: dict[str, list[Decimal]] = defaultdict(list)
    cdp_examples: dict[str, list[str]] = defaultdict(list)
    for o in cdp_obs:
        if o.price_usd is not None:
            cdp_prices[o.category].append(o.price_usd)
        cdp_examples[o.category].append(o.product_or_origin[:80])

    cats: list[DemandCategory] = []
    all_names = sorted(set(by_cat) | set(cdp_prices) | {"monitoring"})
    for name in all_names:
        sellers = by_cat.get(name, [])
        buyers = sum(o.unique_buyers or 0 for o in sellers)
        txs = sum(o.tx_count or 0 for o in sellers)
        vol = sum((o.volume_usd or Decimal("0")) for o in sellers)
        prices = sorted(cdp_prices.get(name, []))
        notable = [
            f"{o.product_or_origin} (buyers={o.unique_buyers}, vol=${o.volume_usd}, shape={o.demand_shape})"
            for o in sorted(sellers, key=lambda x: float(x.volume_usd or 0), reverse=True)[:5]
        ]
        if not notable:
            notable = cdp_examples.get(name, [])[:5]

        # Evidence level: need meaningful observed buyers+txs for E3; volume+breadth for E4
        # Tiny dust activity (e.g. 14 buyers / $0.23) is NOT E3 product demand.
        if sellers and ((buyers >= 30 and txs >= 100) or vol >= Decimal("50")):
            level = DemandEvidenceLevel.E4 if (buyers >= 100 or vol >= Decimal("500")) else DemandEvidenceLevel.E3
        elif sellers and (buyers > 0 or txs > 0):
            # Observable but thin — treat as E2 (pricing/activity trace) not E3 demand
            level = DemandEvidenceLevel.E2
        elif prices:
            level = DemandEvidenceLevel.E2
        elif cdp_examples.get(name) or notable:
            level = DemandEvidenceLevel.E1
        else:
            level = DemandEvidenceLevel.E0

        # Concentration note
        if sellers:
            top = max(sellers, key=lambda o: float(o.volume_usd or 0))
            share = float(top.volume_usd or 0) / float(vol) if vol > 0 else 0
            conc = f"top_origin={top.product_or_origin} ~{share:.0%} of category volume; shapes={Counter(o.demand_shape for o in sellers).most_common(4)}"
        else:
            conc = "no x402scan seller activity attributed to this category in parse"

        cats.append(
            DemandCategory(
                name=name,
                evidence_level=level,
                sellers_observed=len(sellers) or len(cdp_examples.get(name, [])),
                buyers_observed=buyers,
                tx_activity=txs,
                volume_usd=vol,
                typical_price_usd=prices[:8],
                buyer_concentration_note=conc,
                notable_products=notable[:6],
                evidence_urls=[
                    "https://www.x402scan.com/",
                    "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
                ],
                notes=(
                    "Activity metrics from x402scan seller rows; prices from CDP discovery sample. "
                    "Protocol existence alone does not grant E3 to a product idea."
                ),
            )
        )
    cats.sort(key=lambda c: (LEVEL_RANK[c.evidence_level], float(c.volume_usd), c.buyers_observed), reverse=True)
    return cats


def evidence_for_existing_loops(demand_map: list[DemandCategory]) -> list[dict[str, Any]]:
    """Honest rescoring of prior Gen-0 loops."""
    by = {c.name: c for c in demand_map}
    mon = by.get("monitoring")
    enrich = by.get("enrichment")
    chain = by.get("blockchain_rpc_data")
    retr = by.get("retrieval_search")

    def lvl(c: DemandCategory | None) -> DemandEvidenceLevel:
        return c.evidence_level if c else DemandEvidenceLevel.E0

    return [
        {
            "name": "public_change_diff_refinery",
            "evidence_level": lvl(mon).value if mon and LEVEL_RANK[lvl(mon)] >= LEVEL_RANK[DemandEvidenceLevel.E3] else "E1",
            "rationale": (
                "Comparable change-monitor products exist for humans (E1). "
                f"x402 category 'monitoring' evidence={lvl(mon).value if mon else 'E0'}; "
                "little/no machine paid volume observed for change-diff APIs. "
                "Hypothesized $0.02 sell price is NOT observed demand. "
                "Same economic job as material_change_alert_api — not independent evidence."
            ),
            "related_to": ["material_change_alert_api"],
        },
        {
            "name": "material_change_alert_api",
            "evidence_level": lvl(mon).value if mon and LEVEL_RANK[lvl(mon)] >= LEVEL_RANK[DemandEvidenceLevel.E3] else "E1",
            "rationale": (
                "Same seam as public_change_diff_refinery. Competitor monitors prove human willingness "
                "to pay for page changes (E1), not E3 machine spend on this product category in x402scan."
            ),
            "related_to": ["public_change_diff_refinery"],
        },
        {
            "name": "agent_tool_affiliate_broker",
            "evidence_level": "E2",
            "rationale": (
                "Affiliate programs publish rates (E2). No observable machine-buyer spend "
                "for Forage's broker packet product (not E3). Do not bootstrap Shopify on E2 alone."
            ),
            "related_to": [],
        },
        {
            "name": "swarms_mcp_tool_merchant",
            "evidence_level": "E1",
            "rationale": ("Swarms marketplace exists (E1). Marketing claims ≠ per-listing purchase/volume evidence. No E3 extracted from public page parse."),
            "related_to": [],
        },
        {
            "name": "x402_niche_structured_feed_merchant",
            "evidence_level": "E2",
            "rationale": (
                f"x402 paid APIs exist with prices (E2) and ecosystem spend is real, but THIS "
                f"specific feed product had no E3 category match. Chain data E={lvl(chain).value}; "
                f"retrieval E={lvl(retr).value}; enrichment E={lvl(enrich).value}."
            ),
            "related_to": [],
        },
    ]


def propose_adjacent_organisms(demand_map: list[DemandCategory]) -> list[AdjacentOrganism]:
    """Only adjacent to E3+ categories — concentrated around demonstrated spend."""
    strong = [c for c in demand_map if LEVEL_RANK[c.evidence_level] >= LEVEL_RANK[DemandEvidenceLevel.E3]]
    orgs: list[AdjacentOrganism] = []

    chain = next((c for c in strong if c.name == "blockchain_rpc_data"), None)
    enrich = next((c for c in strong if c.name == "enrichment"), None)
    retr = next((c for c in strong if c.name == "retrieval_search"), None)
    # Fallback: if enrichment not E3 from origin classifier, still allow if retrieval/chain strong
    # and CDP shows enrichment pricing — but require parent E3 from map
    if chain:
        tip = chain.typical_price_usd[0] if chain.typical_price_usd else Decimal("0.003")
        orgs.append(
            AdjacentOrganism(
                name="oneshot_wallet_token_snapshot",
                parent_category="blockchain_rpc_data",
                evidence_level=DemandEvidenceLevel.E4 if chain.evidence_level == DemandEvidenceLevel.E4 else DemandEvidenceLevel.E3,
                agent_role=AgentEconomicRole.MERCHANT,
                summary=(
                    "Adjacent to high-buyer chain data (e.g. onesource-style ERC20 balance calls): "
                    "one paid call returning a schema-bound multi-token snapshot for a wallet "
                    "instead of N single-token calls."
                ),
                input_desc="wallet address + optional token list / chain id",
                value_creation="Batch eth_call/RPC reads → normalized balances JSON",
                buyer_distribution="List on x402 bazaar beside existing chain-data sellers",
                transaction="x402 per-call USDC",
                fulfillment="{wallet, chain, balances[], as_of} JSON",
                verified_payment="x402 settlement visible on scan explorers",
                observed_comparable_pricing=f"CDP sample chain endpoints often ~${tip}/call; compress N calls into one",
                estimated_marginal_cost_usd=Decimal("0.002"),
                proposed_price_usd=Decimal("0.01"),
                expected_margin_usd=Decimal("0.008"),
                cheapest_test=(
                    "Offline: pick 1 wallet, fetch 3 token balances via public RPC, time/cost the batch, draft OpenAPI schema; no listing yet ($0–2)"
                ),
                afternoon_budget_usd=Decimal("5"),
                time_to_external_signal_hours=24,
                setup_required="x402 seller wallet only when listing (not for offline cost probe)",
                setup_class=SetupClass.A_NONE,  # offline probe needs no setup
                ongoing_human_minutes=0,
                key_risk="Compete with blockrun/onesource; need clear batching value",
                evidence_urls=chain.evidence_urls + chain.notable_products[:2],
            )
        )

    if enrich or (chain and any("enrich" in (p or "").lower() for p in (chain.notable_products or []))):
        parent = enrich.name if enrich else "blockchain_rpc_data"
        level = enrich.evidence_level if enrich else DemandEvidenceLevel.E3
        prices = (enrich.typical_price_usd if enrich else []) or [Decimal("0.01")]
        orgs.append(
            AdjacentOrganism(
                name="onchain_entity_brief_refinery",
                parent_category=parent,
                evidence_level=level if LEVEL_RANK[level] >= 3 else DemandEvidenceLevel.E3,
                agent_role=AgentEconomicRole.REFINERY,
                summary=(
                    "Adjacent to enrichment spend (stableenrich / nansen-class buyers): "
                    "refine free public explorer/RPC data into a tiny verified entity brief "
                    "(labels, top tokens, last activity) without proprietary firehose resale."
                ),
                input_desc="address or tx hash",
                value_creation="Public RPC + public explorer pages → compact JSON brief",
                buyer_distribution="x402 listing targeted at agent wallets already buying enrichment",
                transaction="x402 per-call",
                fulfillment="schema-bound entity brief JSON",
                verified_payment="x402 settlement",
                observed_comparable_pricing=f"enrichment CDP prices sample={prices[:4]}; seller vols on scan for enrich origins",
                estimated_marginal_cost_usd=Decimal("0.004"),
                proposed_price_usd=Decimal("0.02"),
                expected_margin_usd=Decimal("0.016"),
                cheapest_test=("Build offline brief for 3 addresses from public RPC only; measure wall-clock + estimate cost; no listing ($0–3)"),
                afternoon_budget_usd=Decimal("5"),
                setup_required="none for offline probe",
                setup_class=SetupClass.A_NONE,
                ongoing_human_minutes=0,
                key_risk="License/ToS; must not scrape prohibited sources; commoditized",
                evidence_urls=[
                    "https://www.x402scan.com/",
                    "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
                ],
            )
        )

    if retr:
        tip = retr.typical_price_usd[0] if retr.typical_price_usd else Decimal("0.01")
        orgs.append(
            AdjacentOrganism(
                name="url_to_structured_facts_compressor",
                parent_category="retrieval_search",
                evidence_level=retr.evidence_level,
                agent_role=AgentEconomicRole.REFINERY,
                summary=(
                    "Adjacent to retrieval spend (e.g. Exa contents / search buyers): "
                    "accept URL(s), return only structured facts matching a caller schema — "
                    "compressing fetch+extract that agents currently chain across paid calls."
                ),
                input_desc="url + json_schema of desired fields",
                value_creation="Fetch public URL → extract fields → validate against schema",
                buyer_distribution="x402 bazaar near retrieval sellers",
                transaction="x402 per-call",
                fulfillment="validated facts JSON or error object",
                verified_payment="x402 settlement",
                observed_comparable_pricing=f"retrieval CDP prices often ${tip}+; Exa-class origins show multi-buyer activity on scan",
                estimated_marginal_cost_usd=Decimal("0.005"),
                proposed_price_usd=Decimal("0.02"),
                expected_margin_usd=Decimal("0.015"),
                cheapest_test=("Offline extract for 3 public URLs into a fixed schema; record cost/latency; no listing ($0–3)"),
                afternoon_budget_usd=Decimal("5"),
                setup_required="none for offline probe",
                setup_class=SetupClass.A_NONE,
                ongoing_human_minutes=0,
                key_risk="Extraction quality; robots/ToS; competition from Exa-like APIs",
                evidence_urls=retr.evidence_urls,
            )
        )

    # If we somehow have fewer than 3, add transformation only if E3+ else skip
    for c in strong:
        if len(orgs) >= 3:
            break
        if c.name in {o.parent_category for o in orgs}:
            continue
        if c.name == "payments_transactions":
            continue  # avoid gambling/coin-flip style endpoints
        orgs.append(
            AdjacentOrganism(
                name=f"adjacent_specialization_{c.name}",
                parent_category=c.name,
                evidence_level=c.evidence_level,
                agent_role=AgentEconomicRole.MERCHANT,
                summary=f"Specialize a cheaper/narrower SKU inside demonstrated {c.name} spend.",
                input_desc="category-specific structured input",
                value_creation="Deterministic transform over public/cheap upstream",
                buyer_distribution="x402 bazaar",
                transaction="x402 per-call",
                fulfillment="JSON schema",
                verified_payment="x402 settlement",
                observed_comparable_pricing=str(c.typical_price_usd[:5]),
                estimated_marginal_cost_usd=Decimal("0.003"),
                proposed_price_usd=Decimal("0.015"),
                expected_margin_usd=Decimal("0.012"),
                cheapest_test=f"Offline prototype for {c.name}; no listing ($0–5)",
                afternoon_budget_usd=Decimal("5"),
                setup_class=SetupClass.A_NONE,
                setup_required="none for offline probe",
                ongoing_human_minutes=0,
                key_risk="Generic — prefer named organisms above",
                evidence_urls=c.evidence_urls,
            )
        )

    for o in orgs:
        o.expected_margin_usd = o.proposed_price_usd - o.estimated_marginal_cost_usd
    return orgs[:3]


def justify_bootstrap(orgs: list[AdjacentOrganism]) -> str:
    """At most ONE — and never for Shopify/E2 affiliate. Require E3+ and setup as final blocker."""
    for o in orgs:
        if LEVEL_RANK[o.evidence_level] < LEVEL_RANK[DemandEvidenceLevel.E3]:
            continue
        if o.setup_class == SetupClass.A_NONE:
            continue  # can run offline / no-setup first
        if "shopify" in o.setup_required.lower() or "affiliate" in o.setup_required.lower():
            continue
        exp = o.cheapest_test.lower()
        if any(k in exp for k in ("offline", "no listing", "draft", "prototype")):
            continue
        return (
            "ONE bootstrap request (E3+ organism; setup is final blocker to real buyers):\n"
            f"- Organism: {o.name}\n"
            f"- Parent category: {o.parent_category} ({o.evidence_level.value})\n"
            f"- Setup: {o.setup_required}\n"
            f"- Test: {o.cheapest_test}\n"
            f"- Budget ≤ ${o.afternoon_budget_usd}\n"
        )
    return (
        "No owner bootstrap requested. "
        "Top organisms have offline/no-listing probes that can advance cost evidence "
        "without Shopify/KYC/wallet. Do not bootstrap on E0–E2 product ideas."
    )


def run_offline_cost_probes(orgs: list[AdjacentOrganism]) -> list[dict[str, Any]]:
    """Cheapest external probes: public HTTP only, $0 spend, measure latency."""
    results: list[dict[str, Any]] = []

    def probe_rpc() -> dict[str, Any]:
        import time

        t0 = time.time()
        try:
            with _client() as client:
                # Public Ethereum JSON-RPC (Cloudflare) — free
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [
                        {
                            "to": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                            "data": "0x70a08231000000000000000000000000d8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                        },
                        "latest",
                    ],
                }
                r = client.post("https://ethereum.publicnode.com", json=payload)
                ok = r.status_code == 200 and "result" in r.text
                return {
                    "probe": "public_eth_call_usdt_balance",
                    "ok": ok,
                    "elapsed_s": round(time.time() - t0, 3),
                    "spend_usd": 0,
                    "notes": "Supports oneshot_wallet_token_snapshot marginal-cost estimate",
                    "rpc": "https://ethereum.publicnode.com",
                }
        except Exception as exc:  # noqa: BLE001
            return {"probe": "public_eth_call_usdt_balance", "ok": False, "error": str(exc), "spend_usd": 0}

    def probe_url_fetch() -> dict[str, Any]:
        import time

        t0 = time.time()
        try:
            with _client() as client:
                r = client.get("https://example.com/")
                return {
                    "probe": "public_url_fetch_example_com",
                    "ok": r.status_code == 200,
                    "elapsed_s": round(time.time() - t0, 3),
                    "bytes": len(r.content),
                    "spend_usd": 0,
                    "notes": "Supports url_to_structured_facts_compressor cost floor",
                }
        except Exception as exc:  # noqa: BLE001
            return {"probe": "public_url_fetch_example_com", "ok": False, "error": str(exc), "spend_usd": 0}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(probe_rpc), pool.submit(probe_url_fetch)]
        for fut in as_completed(futs):
            results.append(fut.result())
    return results


def run_follow_the_money() -> dict[str, Any]:
    x402_obs, x402_meta = scrape_x402scan()
    cdp_obs, cdp_meta = scrape_cdp_discovery(200)
    sw_obs, sw_meta = scrape_swarms_marketplace()

    demand_map = build_demand_map(x402_obs, cdp_obs)
    loop_ladder = evidence_for_existing_loops(demand_map)
    organisms = propose_adjacent_organisms(demand_map)
    probes = run_offline_cost_probes(organisms)
    spend = sum(Decimal(str(p.get("spend_usd") or 0)) for p in probes)
    bootstrap = justify_bootstrap(organisms)

    strong = [c for c in demand_map if LEVEL_RANK[c.evidence_level] >= LEVEL_RANK[DemandEvidenceLevel.E3]]
    caveats = {
        "volume_concentration": (
            "x402scan seller volume is extremely top-heavy; top origins can dominate dollar volume "
            "with few unique buyers and huge tx counts (concentrated_hf / possible automation)."
        ),
        "do_not_equate_protocol_with_product_demand": ("x402/Swarms existing ≠ E3 for change-monitoring or affiliate-broker product ideas."),
        "classifier_noise": "Origin URL heuristics mis-file some sellers; treat category tags as coarse.",
        "swarms": "No reliable per-listing purchase counts extracted from marketing HTML.",
    }

    return {
        "campaign": "FOLLOW_THE_MONEY_V1",
        "shopify_bootstrap": "NOT_REQUESTED",
        "overall_x402": x402_meta.get("overall"),
        "sources": {"x402scan": x402_meta, "cdp_discovery": cdp_meta, "swarms": sw_meta},
        "demand_map": [json.loads(c.model_dump_json()) for c in demand_map],
        "strongest_paid_categories": [json.loads(c.model_dump_json()) for c in strong[:8]],
        "concentration_caveats": caveats,
        "top_sellers_by_volume": [json.loads(o.model_dump_json()) for o in sorted(x402_obs, key=lambda x: float(x.volume_usd or 0), reverse=True)[:12]],
        "top_sellers_by_buyers": [json.loads(o.model_dump_json()) for o in sorted(x402_obs, key=lambda x: x.unique_buyers or 0, reverse=True)[:12]],
        "existing_loops_evidence_ladder": loop_ladder,
        "top_organisms": [json.loads(o.model_dump_json()) for o in organisms],
        "external_tests_launched": probes,
        "actual_spend_usd": str(spend),
        "verified_revenue_usd": "0",
        "bootstrap_request": bootstrap,
        "note": ("Reconnaissance pass. Progress = stronger evidence of WHERE money flows. Offline probes are $0. No dashboards/warehouse/orchestration built."),
    }
