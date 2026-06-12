#!/usr/bin/env python3
"""
Per-stock strategy selector. Given a ticker, characterize its dynamics and backtest
a LIBRARY of strategies on its own history, then recommend the best validated one —
or say "no edge, buy & hold / skip." Each stock is different; we let its data choose.

Sizing is held constant across strategies (2% risk, ATR stop, chandelier trail) so we
compare SIGNAL quality, not sizing. Once a strategy type wins, we tune its knobs
(like we did for SOXL) before deploying.

Usage:
  python3 analyze.py AAPL
  python3 analyze.py SOXL --signal SOXX     # leveraged ETF: signal off the index
"""
from __future__ import annotations
import argparse, math, datetime as dt
import backtest as bt

BEAR = ("2021-12-01", "2022-12-31")

P = dict(capital=100_000.0, risk_pct=2.0, atr_len=14, stop_atr=2.0,
         trail=True, chand_atr=3.0, cost_bps=10)


# ---- extra indicators ----
def rsi_series(bars, period=14):
    out = [None] * len(bars); g = [0.0] * len(bars); l = [0.0] * len(bars)
    for i in range(1, len(bars)):
        ch = bars[i]["c"] - bars[i - 1]["c"]
        g[i] = max(ch, 0.0); l[i] = max(-ch, 0.0)
    if len(bars) <= period: return out
    ag = sum(g[1:period + 1]) / period; al = sum(l[1:period + 1]) / period
    out[period] = 100 - 100 / (1 + (ag / al if al else 999))
    for i in range(period + 1, len(bars)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        out[i] = 100 - 100 / (1 + (ag / al if al else 999))
    return out

def prior_extreme(bars, n, hi=True):
    out = [None] * len(bars)
    for i in range(n, len(bars)):
        w = bars[i - n:i]
        out[i] = max(b["h"] for b in w) if hi else min(b["l"] for b in w)
    return out


# ---- generic long-only simulator (entry/exit boolean arrays) ----
def run(dates, bars, entry, exit_, p):
    atr = bt.atr_series(bars, p["atr_len"]); cost = p["cost_bps"] / 10000.0
    cash = p["capital"]; pos = None; pend = None
    eq = []; trades = []; din = 0
    for i in range(len(dates)):
        o, h, l, c = bars[i]["o"], bars[i]["h"], bars[i]["l"], bars[i]["c"]
        if pend and pend[0] == "exit" and pos:
            cash += pos["sh"] * o * (1 - cost); trades.append(o / pos["entry"] - 1); pos = None
        elif pend and pend[0] == "entry" and pos is None:
            sh = pend[1]; cash -= sh * o * (1 + cost)
            pos = {"sh": sh, "entry": o, "stop": pend[2], "hh": h, "atr": atr[i] or 0}
        pend = None
        if pos:
            din += 1; stop = pos["stop"]
            if p["trail"] and pos["atr"]:
                stop = max(stop, pos["hh"] - p["chand_atr"] * pos["atr"])
            if l <= stop:
                fill = min(o, stop); cash += pos["sh"] * fill * (1 - cost)
                trades.append(fill / pos["entry"] - 1); pos = None
            else:
                pos["hh"] = max(pos["hh"], h)
        if pos and exit_[i]:
            pend = ("exit",)
        elif pos is None and entry[i] and atr[i]:
            sd = p["stop_atr"] * atr[i]
            sh = int((cash * p["risk_pct"] / 100) / sd)
            if sh >= 1 and sh * c <= cash:
                pend = ("entry", sh, c - sd)
        eq.append(cash + (pos["sh"] * c if pos else 0))
    m = bt.metrics(dates, eq, trades, din, p["capital"]); m["eq"] = eq
    return m


# ---- strategy library: each returns (entry[], exit[]) ----
def strat_trend(bars):
    ema = bt.ema_series([b["c"] for b in bars], 20); adx = bt.adx_series(bars, 14)
    e = [ema[i] is not None and ema[i - 1] is not None and bars[i]["c"] > ema[i]
         and ema[i] > ema[i - 1] and adx[i] is not None and adx[i] >= 25 for i in range(len(bars))]
    x = [ema[i] is not None and bars[i]["c"] < ema[i] for i in range(len(bars))]
    return e, x

def strat_meanrev(bars):
    rsi = rsi_series(bars, 14); sma = bt.sma_series([b["c"] for b in bars], 200)
    e = [rsi[i] is not None and rsi[i] < 30 and sma[i] is not None and bars[i]["c"] > sma[i]
         for i in range(len(bars))]
    x = [rsi[i] is not None and rsi[i] > 55 for i in range(len(bars))]
    return e, x

def strat_breakout(bars):
    hi = prior_extreme(bars, 20, True); lo = prior_extreme(bars, 10, False)
    e = [hi[i] is not None and bars[i]["c"] > hi[i] for i in range(len(bars))]
    x = [lo[i] is not None and bars[i]["c"] < lo[i] for i in range(len(bars))]
    return e, x

def strat_macross(bars):
    f = bt.ema_series([b["c"] for b in bars], 20); s = bt.ema_series([b["c"] for b in bars], 50)
    e = [f[i] is not None and s[i] is not None and f[i] > s[i] for i in range(len(bars))]
    x = [f[i] is not None and s[i] is not None and f[i] < s[i] for i in range(len(bars))]
    return e, x

LIB = {"trend-follow": strat_trend, "mean-revert": strat_meanrev,
       "breakout": strat_breakout, "ma-cross": strat_macross}


# ---- helpers ----
def sub(eq, lo, hi):
    seg = eq[lo:hi + 1]; peak = -1e18; mdd = 0
    for v in seg:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak if peak > 0 else 0)
    r = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg)) if seg[i - 1] > 0]
    m = sum(r) / len(r) if r else 0; var = sum((x - m) ** 2 for x in r) / len(r) if r else 0
    return mdd, (m / math.sqrt(var) * math.sqrt(252) if var > 0 else 0)

def idx(d, t):
    for i, x in enumerate(d):
        if x >= t: return i
    return len(d) - 1

def buy_hold(dates, bars, cap):
    eq = [cap * bars[i]["c"] / bars[0]["c"] for i in range(len(bars))]
    m = bt.metrics(dates, eq, [eq[-1] / cap - 1], len(bars), cap); m["eq"] = eq
    return m

def characterize(bars):
    r = [bars[i]["c"] / bars[i - 1]["c"] - 1 for i in range(1, len(bars))]
    mean = sum(r) / len(r); sd = (sum((x - mean) ** 2 for x in r) / len(r)) ** 0.5
    # lag-1 autocorrelation: + = momentum/trend, - = mean-reverting
    num = sum((r[i] - mean) * (r[i - 1] - mean) for i in range(1, len(r)))
    den = sum((x - mean) ** 2 for x in r)
    ac = num / den if den else 0
    adx = bt.adx_series(bars, 14); trendy = sum(1 for a in adx if a and a >= 25) / len(bars)
    return dict(ann_vol=sd * math.sqrt(252), ann_ret=(bars[-1]["c"] / bars[0]["c"]) ** (252 / len(bars)) - 1,
                bh_sharpe=mean / sd * math.sqrt(252) if sd else 0, autocorr=ac, trendy=trendy)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--signal", help="signal symbol (default: the ticker itself)")
    ap.add_argument("--years", type=float, default=6)
    args = ap.parse_args(argv)
    sig_sym = args.signal or args.ticker
    key, sec = bt.broker.load_creds()
    start = (dt.date.today() - dt.timedelta(days=int(args.years * 365 + 40))).isoformat()
    px = bt.fetch(args.ticker, start, key, sec)
    sg = bt.fetch(sig_sym, start, key, sec) if sig_sym != args.ticker else px
    # align trade + signal series
    if sig_sym != args.ticker:
        dates, sg, px = bt.align(sg, px)
    else:
        dates = [b["date"] for b in px]
    bl, bh_i = idx(dates, BEAR[0]), idx(dates, BEAR[1])

    c = characterize(px)
    tilt = ("momentum/trend" if c["autocorr"] > 0.02 else
            "mean-reverting" if c["autocorr"] < -0.02 else "neutral/random")
    print(f"\n=== {args.ticker} (signal: {sig_sym}) | {dates[0]}..{dates[-1]} ({len(dates)}d) ===")
    print(f"  ann return {c['ann_ret']*100:.0f}%  ann vol {c['ann_vol']*100:.0f}%  "
          f"B&H Sharpe {c['bh_sharpe']:.2f}")
    print(f"  return autocorrelation {c['autocorr']:+.3f} -> {tilt} | "
          f"trendy (ADX>25) {c['trendy']*100:.0f}% of days\n")

    def mar(m): return m["cagr"] / m["mdd"] if m["mdd"] > 0 else 0.0
    print(f"  {'strategy':14} {'return':>8} {'maxDD':>7} {'Sharpe':>7} {'MAR':>6} {'trades':>6}  {'bearSh':>7}")
    print("  " + "-" * 64)
    results = {}
    bhm = buy_hold(dates, px, P["capital"])
    db, sb = sub(bhm["eq"], bl, bh_i)
    print(f"  {'buy & hold':14} {bhm['ret']*100:>7.0f}% {bhm['mdd']*100:>6.0f}% "
          f"{bhm['sharpe']:>7.2f} {mar(bhm):>6.2f} {'-':>6}  {sb:>7.2f}")
    for name, fn in LIB.items():
        e, x = fn(sg)
        m = run(dates, px, e, x, P)
        results[name] = m
        db, sb = sub(m["eq"], bl, bh_i)
        print(f"  {name:14} {m['ret']*100:>7.0f}% {m['mdd']*100:>6.0f}% "
              f"{m['sharpe']:>7.2f} {mar(m):>6.2f} {m['trades']:>6}  {sb:>7.2f}")
    print("  " + "-" * 64)

    # Drawdown-aware selection. Best ACTIVE strategy by Sharpe (signal quality),
    # then decide vs buy & hold on SURVIVABLE return (MAR) + drawdown tolerability.
    best = max(results, key=lambda n: results[n]["sharpe"])
    bm = results[best]
    db, sb = sub(bm["eq"], bl, bh_i)
    robust = sb > -1.6 and bm["cagr"] > 0
    bh_unsurvivable = bhm["mdd"] >= 0.45        # can't realistically hold through this
    active_better_mar = mar(bm) >= mar(bhm)

    if robust and (active_better_mar or (bh_unsurvivable and bm["mdd"] < 0.35)):
        note = ("buy & hold has a higher raw Sharpe but an UN-SURVIVABLE "
                f"{bhm['mdd']*100:.0f}% drawdown" if bh_unsurvivable and not active_better_mar
                else f"best survivable return (MAR {mar(bm):.2f} vs B&H {mar(bhm):.2f})")
        print(f"\n  >> RECOMMEND: '{best}' — {note}; it cuts drawdown to "
              f"{bm['mdd']*100:.0f}% (vs {bhm['mdd']*100:.0f}%). Next: tune its knobs, deploy as its own bot.")
    elif bhm["mdd"] < 0.35:
        print(f"\n  >> RECOMMEND: BUY & HOLD {args.ticker} — Sharpe {bhm['sharpe']:.2f}, "
              f"tolerable {bhm['mdd']*100:.0f}% drawdown, and no active strategy adds risk-adjusted "
              f"value. Forcing a strategy would be curve-fitting.")
    else:
        print(f"\n  >> RECOMMEND: SKIP {args.ticker} — no active strategy earns its keep "
              f"(best '{best}' MAR {mar(bm):.2f}) and buy & hold's {bhm['mdd']*100:.0f}% drawdown "
              f"is too punishing. Don't trade this one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
