#!/usr/bin/env python3
"""
Performance reviewer for the SOXL bot. Reads journal.jsonl + the live account and
prints (a) a human summary and (b) a machine-readable JSON block the autonomous
operator uses to decide whether — and what — to change.

Usage:
  python3 review.py            # full summary + JSON
  python3 review.py --json     # JSON block only (for the operator)
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import broker

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(HERE, "journal.jsonl")
STATE = os.path.join(HERE, "state.json")


def load_journal() -> list[dict]:
    if not os.path.exists(JOURNAL):
        return []
    out = []
    with open(JOURNAL) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def analyze(rows: list[dict]) -> dict:
    actions = Counter(r.get("action") for r in rows)
    opens = [r for r in rows if r.get("action") in ("OPEN", "DRY_OPEN")]
    closes = [r for r in rows if r.get("action") == "CLOSE_TREND_BREAK"]
    kills = [r for r in rows if r.get("action") == "KILL_SWITCH"]

    # Per-day final SOXL P&L (last record carrying soxl_daily_pnl per date).
    by_day: dict[str, float] = {}
    for r in rows:
        d = (r.get("ts") or "")[:10]
        if "soxl_daily_pnl" in r:
            by_day[d] = r["soxl_daily_pnl"]
    days = sorted(by_day)
    green = sum(1 for d in days if by_day[d] > 0)
    red = sum(1 for d in days if by_day[d] < 0)

    # Chop signal: OPEN quickly followed by a CLOSE/kill (same or next few ticks).
    quick_reversals = 0
    for i, r in enumerate(rows):
        if r.get("action") in ("OPEN",):
            for nxt in rows[i + 1:i + 4]:
                if nxt.get("action") in ("CLOSE_TREND_BREAK", "KILL_SWITCH"):
                    quick_reversals += 1
                    break

    return {
        "ticks": len(rows),
        "action_counts": dict(actions),
        "opens": len(opens), "trend_closes": len(closes), "kill_switches": len(kills),
        "trading_days": len(days), "green_days": green, "red_days": red,
        "cum_realized_by_day": {d: by_day[d] for d in days},
        "quick_reversals": quick_reversals,
        "last_action": rows[-1].get("action") if rows else None,
    }


def diagnose(a: dict, live: dict) -> list[str]:
    """Heuristic flags the operator should consider. Not prescriptions."""
    flags = []
    if a["kill_switches"]:
        flags.append("KILL_SWITCH has tripped — risk per trade or stop may be too "
                     "aggressive (consider lower risk_pct / wider stop_atr).")
    if a["opens"] >= 3 and a["quick_reversals"] / max(a["opens"], 1) >= 0.5:
        flags.append("Many entries reversed quickly — likely chop. Consider a "
                     "stronger trend filter or standing aside (raise EMA confirmation).")
    if a["ticks"] >= 20 and a["opens"] == 0:
        flags.append("Many ticks, zero entries — gate may be too strict "
                     "(ema_len too long, or persistent downtrend: this may be correct).")
    if a["trading_days"] >= 3 and a["red_days"] > a["green_days"] * 2:
        flags.append("Red days dominate — strategy underperforming; investigate "
                     "entry timing vs the underlying trend.")
    if not flags:
        flags.append("No structural problem detected. Default action: DO NOTHING "
                     "(do not change code just to change it).")
    return flags


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = load_journal()
    a = analyze(rows)

    live = {}
    try:
        key, sec = broker.load_creds()
        acct = broker.get_account(key, sec)
        positions = broker.get_positions(key, sec)
        pos = next((p for p in positions if p["symbol"] == "SOXL"), None)
        live = {
            "equity": float(acct["equity"]),
            "buying_power": float(acct["buying_power"]),
            "soxl_position": ({"qty": pos["qty"],
                               "unrealized_intraday_pl": float(pos["unrealized_intraday_pl"])}
                              if pos else None),
        }
    except broker.AlpacaError as e:
        live = {"error": str(e)}

    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    flags = diagnose(a, live)
    blob = {"analysis": a, "live": live, "state": state, "flags": flags}

    if args.json:
        print(json.dumps(blob, indent=2)); return 0

    print("=" * 56)
    print("  SOXL BOT — PERFORMANCE REVIEW")
    print("=" * 56)
    print(f"  ticks logged   : {a['ticks']}   last action: {a['last_action']}")
    print(f"  entries        : {a['opens']}   trend-exits: {a['trend_closes']}   "
          f"kill-switches: {a['kill_switches']}")
    print(f"  trading days   : {a['trading_days']}  (green {a['green_days']} / "
          f"red {a['red_days']})")
    print(f"  quick reversals: {a['quick_reversals']}  (entries chopped out fast)")
    if a["cum_realized_by_day"]:
        print("  SOXL P&L by day:")
        for d, v in a["cum_realized_by_day"].items():
            print(f"     {d}: ${v:,.2f}")
    if "error" in live:
        print(f"  live account   : ERROR {live['error']}")
    else:
        print(f"  live equity    : ${live.get('equity', 0):,.2f}")
        print(f"  SOXL position  : {live.get('soxl_position')}")
    if state:
        print(f"  state          : halted={state.get('halted')} "
              f"day_start_equity={state.get('day_start_equity')}")
    print("-" * 56)
    print("  FLAGS for the operator:")
    for f in flags:
        print(f"   • {f}")
    print("=" * 56)
    print("\nJSON:\n" + json.dumps(blob))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
