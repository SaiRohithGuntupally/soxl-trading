#!/usr/bin/env python3
"""
Research runner: sweep strategy knobs + structural ideas through backtest.py and
check robustness across regimes (2022 bear vs the bull) so we adopt genuine edge,
not a curve-fit to one number.

HONEST CAVEAT baked into how we read this: sweeping many variants on one 6-year
window is data-mining. A single high-Sharpe spike is probably noise. We only trust
a change that (a) sits on a PLATEAU of nearby-good values and (b) holds up in BOTH
the bear and the bull sub-periods.

Usage: python3 experiments.py
"""

from __future__ import annotations

import math
import backtest as bt

# regime windows (semis bear vs the AI bull)
BEAR = ("2021-12-01", "2022-12-31")
BULL = ("2023-01-01", "2026-12-31")


def sub(eq, lo, hi):
    seg = eq[lo:hi + 1]
    if len(seg) < 5:
        return 0, 0, 0
    cap = seg[0]; final = seg[-1]
    peak = -1e18; mdd = 0
    for v in seg:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak if peak > 0 else 0)
    rets = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg)) if seg[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0
    sharpe = mean / math.sqrt(var) * math.sqrt(252) if var > 0 else 0
    return final / cap - 1, mdd, sharpe


def idx(dates, target):
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return len(dates) - 1


def main():
    key, sec = bt.broker.load_creds()
    import datetime as dt
    start = (dt.date.today() - dt.timedelta(days=6 * 365 + 40)).isoformat()
    soxx = bt.fetch("SOXX", start, key, sec)
    soxl = bt.fetch("SOXL", start, key, sec)
    dates, soxx, soxl = bt.align(soxx, soxl)
    bl, bh = idx(dates, BEAR[0]), idx(dates, BEAR[1])
    ul, uh = idx(dates, BULL[0]), idx(dates, BULL[1])
    print(f"Window {dates[0]}..{dates[-1]} ({len(dates)}d) | "
          f"bear {dates[bl]}..{dates[bh]} | bull {dates[ul]}..{dates[-1]}\n")

    BASE = dict(chop=True, trail=True, risk_pct=4.0, ema_len=20, adx_min=25.0,
                stop_atr=1.5, chand_atr=3.0)

    def run(**over):
        p = bt.cfg(**{**BASE, **over})
        return bt.simulate(dates, soxx, soxl, p)

    def line(label, m):
        rb, db, sb = sub(m["eq"], bl, bh)
        ru, du, su = sub(m["eq"], ul, len(dates) - 1)
        print(f"{label:22} full: {m['ret']*100:>5.0f}% DD{m['mdd']*100:>3.0f}% "
              f"Sh{m['sharpe']:>4.2f} | bear Sh{sb:>5.2f} DD{db*100:>3.0f}% | "
              f"bull Sh{su:>4.2f}")

    base_m = run()
    print("BASELINE (current live):")
    line("ema20 adx25 ch3", base_m)
    base_sh = base_m["sharpe"]
    print(f"  -> beat full-Sharpe {base_sh:.2f} AND hold in both regimes to win.\n")

    sweeps = [
        ("EMA length", "ema_len", [10, 15, 20, 25, 30, 40, 50]),
        ("ADX min", "adx_min", [0, 15, 20, 25, 30, 35]),
        ("Initial stop xATR", "stop_atr", [1.0, 1.5, 2.0, 2.5, 3.0]),
        ("Chandelier xATR", "chand_atr", [2.0, 2.5, 3.0, 3.5, 4.0]),
        ("Regime MA (SOXX>SMA)", "regime_ma", [0, 100, 150, 200]),
    ]
    for title, knob, vals in sweeps:
        print(f"== {title} ==")
        for v in vals:
            line(f"{knob}={v}", run(**{knob: v}))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
