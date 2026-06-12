#!/usr/bin/env python3
"""
Walk-forward / out-of-sample validation. The honest answer to "is the strategy we
picked actually real for this stock, or did we curve-fit it in-sample?"

Method (anchored single split):
  - Run each library strategy on the FULL series (so indicators have warmup).
  - SELECT the best strategy using ONLY the first ~67% of the equity curve
    (in-sample, IS) — drawdown-aware (MAR), exactly how analyze.py chooses.
  - Then MEASURE that selected strategy on the held-out last ~33% (out-of-sample,
    OOS) — data the selection never used. That OOS number is the unbiased estimate
    of forward edge.
  - Verdict: HOLDS / WEAKENS / FAILS, and whether it still beats buy & hold OOS.

Usage:
  python3 walkforward.py                 # the 7 deployed bots
  python3 walkforward.py MSTR            # one ticker (self-signal)
  python3 walkforward.py SOXL SOXX       # ticker + signal
"""
from __future__ import annotations
import math, datetime as dt
import analyze as az
import backtest as bt

DEPLOYED = [("SOXL", "SOXX"), ("MSTR", None), ("PLTR", None), ("TNA", "IWM"),
            ("UPRO", "SPY"), ("TQQQ", "QQQ"), ("LABU", "XBI")]


def slice_metrics(eq, lo, hi):
    seg = eq[lo:hi + 1]
    if len(seg) < 5:
        return dict(ret=0, cagr=0, mdd=0, sharpe=0, mar=0)
    cap, final = seg[0], seg[-1]
    years = len(seg) / 252
    cagr = (final / cap) ** (1 / years) - 1 if final > 0 and years > 0 else -1
    peak = -1e18; mdd = 0
    for v in seg:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak if peak > 0 else 0)
    r = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg)) if seg[i - 1] > 0]
    m = sum(r) / len(r) if r else 0
    var = sum((x - m) ** 2 for x in r) / len(r) if r else 0
    sharpe = m / math.sqrt(var) * math.sqrt(252) if var > 0 else 0
    return dict(ret=final / cap - 1, cagr=cagr, mdd=mdd, sharpe=sharpe,
                mar=(cagr / mdd if mdd > 0 else 0))


def evaluate(ticker, signal=None, years=6, split=0.67):
    sig_sym = signal or ticker
    key, sec = bt.broker.load_creds()
    start = (dt.date.today() - dt.timedelta(days=int(years * 365 + 40))).isoformat()
    px = bt.fetch(ticker, start, key, sec)
    sg = bt.fetch(sig_sym, start, key, sec) if sig_sym != ticker else px
    if sig_sym != ticker:
        dates, sg, px = bt.align(sg, px)
    else:
        dates = [b["date"] for b in px]
    n = len(dates); cut = int(n * split)

    # run each strategy on full data, slice IS / OOS
    curves = {}
    for name, fn in az.LIB.items():
        e, x = fn(sg)
        curves[name] = az.run(dates, px, e, x, az.P)["eq"]
    bh = az.buy_hold(dates, px, az.P["capital"])["eq"]

    is_m = {nm: slice_metrics(c, 0, cut) for nm, c in curves.items()}
    oos_m = {nm: slice_metrics(c, cut, n - 1) for nm, c in curves.items()}
    bh_oos = slice_metrics(bh, cut, n - 1)

    # SELECT on IS only: best active strategy by IS MAR (drawdown-aware)
    sel = max(is_m, key=lambda nm: is_m[nm]["mar"])
    isel, osel = is_m[sel], oos_m[sel]

    # verdict
    if osel["mar"] > 0.15 and osel["sharpe"] > 0.3:
        verdict = "HOLDS"
    elif osel["mar"] > 0 and osel["sharpe"] > 0:
        verdict = "WEAKENS"
    else:
        verdict = "FAILS"
    beat_bh = " (beats B&H OOS)" if osel["mar"] >= bh_oos["mar"] else " (B&H better OOS)"

    print(f"\n{ticker} (signal {sig_sym}) | IS {dates[0]}..{dates[cut]} | "
          f"OOS {dates[cut]}..{dates[-1]}")
    print(f"  selected on IS: '{sel}'  IS[ret {isel['ret']*100:.0f}% DD {isel['mdd']*100:.0f}% "
          f"Sh {isel['sharpe']:.2f} MAR {isel['mar']:.2f}]")
    print(f"  --> OOS forward: ret {osel['ret']*100:.0f}% DD {osel['mdd']*100:.0f}% "
          f"Sh {osel['sharpe']:.2f} MAR {osel['mar']:.2f}  | B&H OOS MAR {bh_oos['mar']:.2f}")
    print(f"  VERDICT: {verdict}{beat_bh}")
    return verdict


def main(argv):
    if argv:
        evaluate(argv[0], argv[1] if len(argv) > 1 else None)
    else:
        print("WALK-FORWARD: select strategy on first ~4y, test on held-out last ~2y")
        tally = {}
        for t, s in DEPLOYED:
            v = evaluate(t, s)
            tally[v] = tally.get(v, 0) + 1
        print(f"\n=== {tally} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
