"""
Shared portfolio ledger + account-level circuit breaker across all bots.

Each bot records its own symbol's daily P&L to portfolio.json (at the repo root).
Any bot can then check whether the COMBINED loss across all bots for the day has
breached the portfolio cap — if so, every bot halts for the day. This bounds
correlated drawdowns (e.g. a market-wide selloff hitting all bots at once).

Best-effort + atomic-ish writes; cron staggering keeps races rare. Never raises.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "portfolio.json")


def _read() -> dict:
    try:
        with open(LEDGER) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record(symbol: str, date: str, pnl: float, base: float) -> None:
    """Record this bot's SOXL-style daily P&L and its capital base."""
    try:
        d = _read()
        d[symbol] = {"date": date, "pnl": round(float(pnl), 2), "base": round(float(base), 2)}
        tmp = LEDGER + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(d, fh, indent=2)
        os.replace(tmp, LEDGER)
    except Exception:
        pass


def combined(date: str) -> tuple[float, float]:
    """(sum of today's P&L across bots, representative capital base)."""
    d = _read()
    total = 0.0
    base = 0.0
    for v in d.values():
        if v.get("date") == date:
            total += float(v.get("pnl", 0))
            base = max(base, float(v.get("base", 0)))
    return total, base


def breached(date: str, cap_pct: float) -> tuple[bool, float, float]:
    """True if combined daily loss <= -cap_pct% of the capital base."""
    total, base = combined(date)
    if base <= 0 or cap_pct <= 0:
        return False, total, base
    return total <= -(cap_pct / 100.0) * base, total, base
