"""Forage local CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from forage.flow import default_db_path, run_campaign, run_demo
from forage.ledger import Ledger
from forage.mechanism_campaign import run_mechanism_generation
from forage.models import ExperimentStatus, FlowPhase
from forage.policy import load_policy

app = typer.Typer(add_completion=False, no_args_is_help=True)
campaign_app = typer.Typer(add_completion=False, no_args_is_help=True)
mechanisms_app = typer.Typer(add_completion=False, no_args_is_help=True)
gen1_app = typer.Typer(add_completion=False, no_args_is_help=True)
app.add_typer(campaign_app, name="campaign")
app.add_typer(mechanisms_app, name="mechanisms")
app.add_typer(gen1_app, name="gen1")


@app.command("demo")
def demo(
    db: Path | None = typer.Option(None, help="SQLite path (default: ephemeral under data/)"),
) -> None:
    """Run deterministic mocked vertical-slice demo."""
    result = run_demo(db)
    typer.echo(json.dumps(result, indent=2))


@mechanisms_app.command("run")
def mechanisms_run(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
    demo_mode: bool = typer.Option(False, "--demo", help="Use mocked research hits"),
) -> None:
    """Gen-0 Opportunity Analyst: mechanisms → rank → 3 finalists → parallel cheap tests."""
    report = run_mechanism_generation(db_path=db, demo=demo_mode)
    typer.echo(f"mechanisms_considered={len(report.get('considered', []))}")
    typer.echo(f"finalists={[f.get('mechanism_type') for f in report.get('finalists', [])]}")
    for t in report.get("tests", []):
        typer.echo(f"test {t.get('type')} launched={t.get('launched')} status={t.get('status')} bootstrap={t.get('needs_bootstrap')}")
    bootstrap = report.get("bootstrap_request") or ""
    if bootstrap:
        typer.echo("---")
        typer.echo(bootstrap)
    typer.echo("---")
    typer.echo(f"totals={report.get('totals')}")
    # Write full report beside ledger for inspection
    out = Path(db).with_suffix(".gen0.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    typer.echo(f"report_path={out}")


@mechanisms_app.command("hunt")
def mechanisms_hunt(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path (for evidence notes)"),
    limit_fetch: int = typer.Option(18, help="Max program pages to fetch"),
) -> None:
    """Hunt concrete live paid opportunities; rank with 0–5 rubric; no KYC ask unless justified."""
    from forage.opportunities import hunt_concrete_opportunities, opportunities_report, top_opportunities

    opps = hunt_concrete_opportunities(limit_fetch=limit_fetch)
    top = top_opportunities(opps, k=3)
    report = opportunities_report(opps, top)
    out = Path(db).with_name("opportunities.gen0.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    typer.echo(f"found={len(opps)} eligible={sum(1 for o in opps if o.eligible)}")
    for o in top:
        typer.echo(
            f"TOP {o.platform} | {o.title[:70]} | "
            f"${o.payout_usd_low}-{o.payout_usd_high} | rank={o.rank_score} | "
            f"auton={o.autonomy_confidence} | setup={o.setup_class.value}"
        )
        typer.echo(f"  url={o.source_url}")
    typer.echo("---")
    typer.echo(report["bootstrap_request"])
    typer.echo(f"report_path={out}")


@mechanisms_app.command("sprint")
def mechanisms_sprint(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
    max_inspect: int = typer.Option(1000, help="Stop after N listings inspected"),
) -> None:
    """Bounded Gen-0 viability sprint: hard gate; no KYC unless a finalist needs it."""
    from forage.viability_sprint import run_viability_sprint

    report = run_viability_sprint(max_inspect=max_inspect)
    out = Path(db).with_name("viability_sprint.gen0.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    # Durable sprint finding on ledger meta (no architecture change)
    policy = load_policy(Path("policy.yaml"))
    ledger = Ledger(db, policy)
    if report.get("finding"):
        ledger.set_meta("ZERO_SETUP_OBJECTIVE_TASK_MARKET", "SPARSE")
        ledger.set_meta("gen0_sprint_finding", report["finding"])
        if report.get("relax_one_constraint"):
            ledger.set_meta("gen0_sprint_relax_one", report["relax_one_constraint"])
    ledger.set_meta(
        "gen0_viability_sprint",
        json.dumps({k: report[k] for k in report if k != "all"}, default=str)[:100000],
    )
    typer.echo(
        f"inspected={report['inspected']} materialized={report['opportunities_materialized']} "
        f"viable={report['viable_count']} stopped={report['stopped_reason']} "
        f"elapsed_h={report['elapsed_hours']}"
    )
    if report.get("finding"):
        typer.echo(report["finding"])
    if report.get("relax_one_constraint"):
        typer.echo(report["relax_one_constraint"])
    for o in report.get("top3") or []:
        typer.echo(
            f"TOP {o.get('platform')} | {str(o.get('title', ''))[:70]} | "
            f"${o.get('payout_usd_low')}-{o.get('payout_usd_high')} | "
            f"auton={o.get('autonomy_confidence')} rank={o.get('rank_score')}"
        )
        typer.echo(f"  url={o.get('source_url')}")
    typer.echo("---")
    typer.echo(report.get("bootstrap_request") or "")
    typer.echo(f"report_path={out}")


@mechanisms_app.command("loops")
def mechanisms_loops(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
) -> None:
    """ECONOMIC_LOOP_DISCOVERY_V1: merchant/refinery loops → top 3 afternoon experiments."""
    from forage.economic_loop_campaign import run_economic_loop_discovery

    report = run_economic_loop_discovery(db_path=db)
    out = Path(db).with_name("economic_loops.gen0.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    typer.echo(f"campaign={report.get('campaign')} worker_hunt={report.get('worker_listing_hunt')}")
    typer.echo(f"considered={len(report.get('considered', []))} eligible={sum(1 for L in report.get('considered', []) if L.get('eligible'))}")
    for L in report.get("top3") or []:
        typer.echo(
            f"TOP [{L.get('scout')}/{L.get('agent_role')}] {L.get('name')} | "
            f"margin=${L.get('contribution_margin_usd')} rank={L.get('rank_score')} | "
            f"budget≤${L.get('afternoon_budget_usd')}"
        )
        typer.echo(f"  experiment: {str(L.get('afternoon_experiment', ''))[:120]}")
    typer.echo("---")
    typer.echo(report.get("bootstrap_request") or "")
    typer.echo(f"report_path={out}")


@mechanisms_app.command("money")
def mechanisms_money(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
) -> None:
    """FOLLOW_THE_MONEY_V1: demand map from observed machine spend; no Shopify bootstrap."""
    from forage.follow_the_money_campaign import run_follow_the_money_campaign

    report = run_follow_the_money_campaign(db_path=db)
    out = Path(db).with_name("follow_the_money.gen0.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    overall = report.get("overall_x402") or {}
    typer.echo(f"campaign={report.get('campaign')} shopify={report.get('shopify_bootstrap')}")
    if overall:
        typer.echo(
            f"x402_overall txs={overall.get('transactions')} "
            f"vol=${overall.get('volume_usd')} "
            f"buyers={overall.get('unique_buyers')} sellers={overall.get('unique_sellers')}"
        )
    typer.echo("strongest_categories:")
    for c in report.get("strongest_paid_categories") or []:
        typer.echo(f"  [{c.get('evidence_level')}] {c.get('name')} buyers={c.get('buyers_observed')} txs={c.get('tx_activity')} vol=${c.get('volume_usd')}")
    typer.echo("existing_loops_ladder:")
    for row in report.get("existing_loops_evidence_ladder") or []:
        typer.echo(f"  {row.get('name')}: {row.get('evidence_level')}")
    typer.echo("top_organisms:")
    for o in report.get("top_organisms") or []:
        typer.echo(f"  [{o.get('evidence_level')}] {o.get('name')} parent={o.get('parent_category')} margin=${o.get('expected_margin_usd')}")
        typer.echo(f"    test: {str(o.get('cheapest_test', ''))[:110]}")
    typer.echo(f"spend=${report.get('actual_spend_usd')} revenue=${report.get('verified_revenue_usd')}")
    typer.echo("---")
    typer.echo(report.get("bootstrap_request") or "")
    typer.echo(f"report_path={out}")


@gen1_app.command("specificity")
def gen1_specificity() -> None:
    """Bounded specificity check for A/B/C vs current x402 analogs."""
    from forage.gen1.specificity import run_specificity_check

    report = run_specificity_check()
    for name, body in (report.get("organisms") or {}).items():
        typer.echo(f"{name}: {body.get('status')} price=${body.get('fixed_price_usd')}")
        typer.echo(f"  differs: {body.get('differs')}")
    typer.echo("report_path=data/gen1_specificity.json")


@gen1_app.command("selftest")
def gen1_selftest(
    base_url: str | None = typer.Option(
        None,
        help="If set, probe unpaid routes expecting HTTP 402 (e.g. http://127.0.0.1:4021)",
    ),
) -> None:
    """Fulfillment + optional 402 plumbing. SELF_TEST never promotes to E5/E6."""
    from forage.gen1.selftest import run_selftest

    report = run_selftest(base_url=base_url)
    typer.echo(json.dumps(report, indent=2, default=str))


@gen1_app.command("serve")
def gen1_serve(
    host: str = typer.Option(None, help="Bind host (default FORAGE_GEN1_HOST or 127.0.0.1)"),
    port: int = typer.Option(None, help="Bind port (default FORAGE_GEN1_PORT or 4021)"),
    payments: bool = typer.Option(True, help="Enable x402 PaymentMiddlewareASGI"),
) -> None:
    """Serve A/B/C on one tiny FastAPI process (testnet by default)."""
    import os

    import uvicorn

    from forage.gen1.app import build_app

    if host is None:
        host = os.environ.get("FORAGE_GEN1_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("FORAGE_GEN1_PORT", "4021"))
    application = build_app(enable_payments=payments)
    typer.echo(f"serving gen1 on http://{host}:{port} payments={payments}")
    uvicorn.run(application, host=host, port=port, reload=False)


@gen1_app.command("serve-ch")
def gen1_serve_ch(
    host: str = typer.Option(None, help="Bind host (default FORAGE_CH_HOST or 127.0.0.1)"),
    port: int = typer.Option(None, help="Bind port (default FORAGE_CH_PORT or 4022)"),
    payments: bool = typer.Option(True, help="Enable x402 PaymentMiddlewareASGI"),
) -> None:
    """Serve UK_COMPANY_COUNTERPARTY_PREFLIGHT on an isolated process (default :4022)."""
    import os

    import uvicorn

    from forage.gen1.ch_app import build_ch_app

    if host is None:
        host = os.environ.get("FORAGE_CH_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("FORAGE_CH_PORT", "4022"))
    application = build_ch_app(enable_payments=payments)
    typer.echo(f"serving uk_company_counterparty_preflight on http://{host}:{port} payments={payments}")
    uvicorn.run(application, host=host, port=port, reload=False)


@gen1_app.command("serve-sib")
def gen1_serve_sib(
    host: str = typer.Option(None, help="Bind host (default FORAGE_SIB_HOST or 127.0.0.1)"),
    port: int = typer.Option(None, help="Bind port (default FORAGE_SIB_PORT or 4023)"),
    payments: bool = typer.Option(True, help="Enable x402 PaymentMiddlewareASGI"),
) -> None:
    """Serve sibling SKUs (resource preflight + capability resolver) isolated from CH."""
    import os

    import uvicorn

    from forage.gen1.sib_app import build_sib_app

    if host is None:
        host = os.environ.get("FORAGE_SIB_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("FORAGE_SIB_PORT", "4023"))
    application = build_sib_app(enable_payments=payments)
    typer.echo(f"serving sibling SKUs on http://{host}:{port} payments={payments}")
    uvicorn.run(application, host=host, port=port, reload=False)


@gen1_app.command("stamp-sib-listed")
def gen1_stamp_sib_listed(
    public_base_url: str = typer.Argument(..., help="Public HTTPS base URL"),
    pay_to: str = typer.Option(..., help="Receiver address used on Bazaar"),
    force: bool = typer.Option(False, help="Stamp even if Bazaar not all visible"),
) -> None:
    """Record sibling listed_at only after Bazaar visibility. Does not touch CH."""
    from forage.gen1.sib_discovery import stamp_sib_listed

    report = stamp_sib_listed(public_base_url, pay_to, force=force)
    typer.echo(json.dumps(report, indent=2, default=str))


@gen1_app.command("mcp-preflight")
def gen1_mcp_preflight() -> None:
    """MCP server for check_x402_payment_readiness. Does not restart sibling Bazaar clocks."""
    from forage.gen1.mcp_preflight import main as mcp_main

    mcp_main()


@gen1_app.command("mark-listed")
def gen1_mark_listed(
    public_base_url: str = typer.Argument(..., help="Public HTTPS base URL once reachable"),
) -> None:
    """Record discovery/listing timestamp locally (no public admin endpoint)."""
    from datetime import datetime, timezone
    from pathlib import Path

    ts = datetime.now(timezone.utc).isoformat()
    path = Path("data/gen1/listed.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"listed_at": ts, "public_base_url": public_base_url.rstrip("/")}
    path.write_text(json.dumps(payload, indent=2))
    typer.echo(json.dumps(payload, indent=2))


@gen1_app.command("status")
def gen1_status() -> None:
    """Gen-1 metrics: external buyers, revenue, listing, bootstrap."""
    from forage.gen1.status import build_status_report

    report = build_status_report()
    typer.echo(json.dumps(report, indent=2, default=str))
    if report.get("bootstrap_required"):
        typer.echo("---")
        typer.echo(
            "BOOTSTRAP: Provide one publicly reachable HTTPS base URL for the Gen-1 "
            "process (or approve temporary tunnel/hosting). Testnet plumbing is ready; "
            "bazaar discovery requires a non-localhost resource URL. "
            "Do not flip policy dry_run for mainnet settlement until that URL exists."
        )


@campaign_app.command("run")
def campaign_run(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
    demo_mode: bool = typer.Option(False, "--demo", help="Use mocked research"),
) -> None:
    """Discover opportunities and prepare/run bounded experiments (policy DRY RUN by default)."""
    flow = run_campaign(db_path=db, demo=demo_mode)
    ledger: Ledger = flow.services["ledger"]
    typer.echo(f"campaign_id={flow.state.campaign_id}")
    typer.echo(f"phase={flow.state.phase}")
    typer.echo(f"experiments={flow.state.active_experiment_ids}")
    typer.echo(f"decision={flow.state.last_decision}")
    setup = ledger.get_meta("setup_request")
    if setup:
        typer.echo("---")
        typer.echo(setup)
    typer.echo("---")
    typer.echo(f"candidates_meta={ledger.get_meta('gen0_candidates')[:2000]}")
    typer.echo(f"totals={ {k: str(v) for k, v in ledger.totals().items()} }")


@campaign_app.command("status")
def campaign_status(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
) -> None:
    policy = load_policy(Path("policy.yaml"))
    if not db.exists():
        typer.echo("no ledger yet; run: forage campaign run")
        raise typer.Exit(1)
    ledger = Ledger(db, policy)
    for row in ledger.list_experiments():
        if row["id"] == "campaign":
            continue
        typer.echo(f"{row['id']} status={row['status']} phase={row['phase']} budget={row['budget_usd']} deadline={row['deadline']}")
    setup = ledger.get_meta("setup_request")
    if setup:
        typer.echo("---")
        typer.echo(setup)
    typer.echo(f"totals={ {k: str(v) for k, v in ledger.totals().items()} }")


@campaign_app.command("pause")
def campaign_pause(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
) -> None:
    policy = load_policy(Path("policy.yaml"))
    ledger = Ledger(db, policy)
    for row in ledger.list_experiments():
        if row["id"] == "campaign":
            continue
        if row["status"] in (ExperimentStatus.RUNNING.value, ExperimentStatus.WAITING_EXTERNAL.value):
            ledger.set_experiment_state(row["id"], status=ExperimentStatus.PAUSED, phase=FlowPhase.PAUSE)
            typer.echo(f"paused {row['id']}")


@campaign_app.command("resume")
def campaign_resume(
    db: Path = typer.Option(default_db_path(), help="SQLite ledger path"),
) -> None:
    policy = load_policy(Path("policy.yaml"))
    ledger = Ledger(db, policy)
    for row in ledger.list_experiments():
        if row["id"] == "campaign":
            continue
        if row["status"] == ExperimentStatus.PAUSED.value:
            ledger.set_experiment_state(row["id"], status=ExperimentStatus.RUNNING, phase=FlowPhase.CHEAP_TEST)
            typer.echo(f"resumed {row['id']}")


if __name__ == "__main__":
    app()
