"""Controlled-wallet denylist — SELF_TEST / owner wallets never count as E5/E6."""

from __future__ import annotations

import json
from pathlib import Path

DENY_PATH = Path("data/gen1/controlled_wallets.json")
EXTRA_DENY_PATHS = (
    Path("data/gen1_sib/controlled_wallets.json"),
    Path("data/gen1_ch/controlled_wallets.json"),
)


def _paths() -> list[Path]:
    seen: list[Path] = []
    for p in (DENY_PATH, *EXTRA_DENY_PATHS):
        if p not in seen:
            seen.append(p)
    return seen


def _load() -> set[str]:
    wallets: set[str] = set()
    for path in _paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            wallets |= {str(x).lower() for x in data.get("wallets", [])}
        except Exception:
            continue
    return wallets


def _save(wallets: set[str]) -> None:
    payload = json.dumps({"wallets": sorted(wallets)}, indent=2)
    for path in _paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload)
        except OSError:
            continue


def register_controlled(wallet: str) -> None:
    w = wallet.lower()
    s = _load()
    s.add(w)
    _save(s)


def is_controlled(wallet: str | None) -> bool:
    if not wallet:
        return False
    return wallet.lower() in _load()


def evidence_class(payer: str | None, *, forced_self_test: bool = False) -> str:
    """Return SELF_TEST | EXTERNAL_CANDIDATE | UNKNOWN — never auto-promotes to E5/E6."""
    if forced_self_test:
        return "SELF_TEST"
    if not payer:
        return "UNKNOWN"
    if is_controlled(payer):
        return "SELF_TEST"
    return "EXTERNAL_CANDIDATE"
