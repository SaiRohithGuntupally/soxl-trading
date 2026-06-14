#!/usr/bin/env python3
"""
Implement + honestly test GRID trading — the canonical "crypto-twitter bot."
Buy a unit each time price drops to a lower grid level; sell it one level up.
Profits from oscillation in a RANGE; theory says it bleeds in strong trends.

We test it with walk-forward (IS first ~4y / OOS held-out last ~2y) vs buy & hold,
across a trending leveraged ETF, a broad index, and a steadier asset — to see if
the "X bot" reality matches the marketing.

Usage: python3 grid.py
"""
from __future__ import annotations
import datetime as dt
import backtest as bt
from walkforward import slice_metrics

def grid_sim(dates, bars, G=10, band=0.25, episode=252, cost_bps=10):
    cap = 100_000.0; cash = cap; cost = cost_bps / 10000.0
    per_grid = cap / G
    levels = []; slots = {}; ep_start = 0
    def set_range(i):
        nonlocal levels, slots, ep_start
        p = bars[i]["c"]; lo, hi = p * (1 - band), p * (1 + band)
        levels = [lo + (hi - lo) * k / G for k in range(G + 1)]
        slots = {}; ep_start = i
    set_range(0)
    eq = []; trades = []
    for i in range(len(dates)):
        o, h, l, c = bars[i]["o"], bars[i]["h"], bars[i]["l"], bars[i]["c"]
        for k in range(G):                       # buys at lower levels touched today
            lv = levels[k]
            if k not in slots and l <= lv:
                sh = int(per_grid / lv)
                if sh >= 1 and cash >= sh * lv * (1 + cost):
                    cash -= sh * lv * (1 + cost); slots[k] = (sh, lv)
        for k in list(slots):                    # sell one level up
            tgt = levels[k + 1]
            if h >= tgt:
                sh, bp = slots.pop(k); cash += sh * tgt * (1 - cost)
                trades.append(tgt / bp - 1)
        if i - ep_start >= episode:              # reset range each ~year
            for k in list(slots):
                sh, bp = slots.pop(k); cash += sh * c * (1 - cost)
            set_range(i)
        eq.append(cash + sum(sh * c for sh, _ in slots.values()))
    m = bt.metrics(dates, eq, trades, len(eq), cap); m["eq"] = eq
    return m

def main():
    key, sec = bt.broker.load_creds()
    start = (dt.date.today() - dt.timedelta(days=6 * 365 + 40)).isoformat()
    print("GRID trading — walk-forward (IS ~4y select-free / OOS held-out ~2y) vs buy & hold\n")
    print(f"  {'ticker':7} {'window':9} {'grid ret':>9} {'grid DD':>8} {'grid Sh':>8} "
          f"{'gridMAR':>8} | {'B&H ret':>8} {'B&H MAR':>8}")
    print("  " + "-" * 78)
    for t in ["SOXL", "TQQQ", "IWM", "GLD", "EEM"]:
        try:
            bars = bt.fetch(t, start, key, sec)
        except Exception as e:
            print(f"  {t}: fetch failed ({e})"); continue
        dates = [b["date"] for b in bars]
        n = len(dates); cut = int(n * 0.67)
        g = grid_sim(dates, bars); bh = bt.metrics(dates,
            [100000 * b["c"] / bars[0]["c"] for b in bars], [1], n, 100000)
        bh["eq"] = [100000 * b["c"] / bars[0]["c"] for b in bars]
        for lbl, lo, hi in [("FULL", 0, n - 1), ("IS", 0, cut), ("OOS", cut, n - 1)]:
            gm = slice_metrics(g["eq"], lo, hi); bm = slice_metrics(bh["eq"], lo, hi)
            print(f"  {t:7} {lbl:9} {gm['ret']*100:>8.0f}% {gm['mdd']*100:>7.0f}% "
                  f"{gm['sharpe']:>8.2f} {gm['mar']:>8.2f} | {bm['ret']*100:>7.0f}% {bm['mar']:>8.2f}")
        print()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
