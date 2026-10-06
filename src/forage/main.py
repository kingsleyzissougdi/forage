"""CrewAI entrypoints (upstream scaffold convention)."""

from __future__ import annotations

from forage.flow import ExperimentCampaignFlow, build_services, run_campaign


def kickoff() -> None:
    run_campaign()


def plot() -> None:
    flow = ExperimentCampaignFlow()
    flow.services = build_services(demo=True)
    flow.plot("forage_flow")


if __name__ == "__main__":
    kickoff()
