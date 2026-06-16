#!/usr/bin/env python3
"""
Test DCA-on-drops ("buy the dip" / safety-order DCA bot) on SOXL to answer:
what % drop is the best trigger? Base order + a safety order each time price falls
`drop` below the last buy, optional take-profit at `tp` above average cost.

This is AVERAGING DOWN on a 3x ETF — a drawdown bomb that only pays if you survive
to the recovery. We report maxDD prominently and walk-forward (IS/OOS) so the
in-sample 'winner' doesn't fool us.

Usage: python3 dca.py
"""
from __future__ import annotations
import datetime as dt
import backtest as bt
from walkforward import slice_metrics

def dca_sim(dates, bars, drop=0.10, tp=0.15, max_orders=8, hold=False, cost_bps=10):
    cap = 100_000.0; cash = cap; cost = cost_bps / 10000.0
    tranche = cap / (max_orders + 1)
    shares = 0.0; basis = 0.0; last_buy = None; n = 0
    def buy(price):
        nonlocal cash, shares, basis, last_buy, n
        amt = min(tranche, cash)
        if amt < 1:
            return
        sh = amt / (price * (1 + cost))
        cash -= sh * price * (1 + cost); shares += sh; basis += sh * price
        last_buy = price; n += 1
    buy(bars[0]["c"])                       # base order
    eq = []
    for i in range(len(dates)):
        c, l, h = bars[i]["c"], bars[i]["l"], bars[i]["h"]
        while last_buy and l <= last_buy * (1 - drop) and n <= max_orders and cash > tranche * 0.5:
            buy(last_buy * (1 - drop))      # safety orders into the drop
        if (not hold) and shares > 0:
            avg = basis / shares
            if h >= avg * (1 + tp):         # take profit, reset cycle
                cash += shares * avg * (1 + tp) * (1 - cost)
                shares = 0.0; basis = 0.0; last_buy = None; n = 0
                buy(c)
        eq.append(cash + shares * c)
    m = bt.metrics(dates, eq, [], len(eq), cap); m["eq"] = eq
    return m

def main():
    key, sec = bt.broker.load_creds()
    start = (dt.date.today() - dt.timedelta(days=6 * 365 + 40)).isoformat()
    bars = bt.fetch("SOXL", start, key, sec)
    dates = [b["date"] for b in bars]; n = len(dates); cut = int(n * 0.67)
    bh = [100000 * b["c"] / bars[0]["c"] for b in bars]
    print(f"SOXL DCA-on-drops sweep | {dates[0]}..{dates[-1]}  (cut at {dates[cut]})\n")
    print(f"  {'variant':26} {'FULL ret':>9} {'maxDD':>7} {'MAR':>6} | {'OOS ret':>8} {'OOS DD':>7} {'OOS MAR':>8}")
    print("  " + "-" * 78)
    def line(lbl, m):
        f = slice_metrics(m["eq"], 0, n - 1); o = slice_metrics(m["eq"], cut, n - 1)
        print(f"  {lbl:26} {f['ret']*100:>8.0f}% {f['mdd']*100:>6.0f}% {f['mar']:>6.2f} | "
              f"{o['ret']*100:>7.0f}% {o['mdd']*100:>6.0f}% {o['mar']:>8.2f}")
    for d in (0.05, 0.08, 0.10, 0.15, 0.20):
        line(f"DCA drop {int(d*100)}% +15% TP", dca_sim(dates, bars, drop=d, tp=0.15))
    print("  -- accumulate-and-hold (no take-profit) --")
    for d in (0.08, 0.10, 0.15):
        line(f"DCA drop {int(d*100)}% hold", dca_sim(dates, bars, drop=d, hold=True))
    print("  " + "-" * 78)
    line("buy & hold SOXL", {"eq": bh})
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
