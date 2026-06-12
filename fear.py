#!/usr/bin/env python3
"""Test a volatility 'fear' gate: block new entries when SOXX realized volatility
spikes. This is the deterministic, backtestable cousin of social-media sentiment.
Compared on top of the current live config (chop+trail+SPY, risk4, ~10bps cost)."""
import math, datetime as dt
import backtest as bt

BEAR = ("2021-12-01", "2022-12-31")

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

def realized_vol(bars, n=10):
    rets = [None] + [bars[i]["c"] / bars[i - 1]["c"] - 1 for i in range(1, len(bars))]
    out = [None] * len(bars)
    for i in range(n, len(bars)):
        w = rets[i - n + 1:i + 1]
        m = sum(w) / len(w); var = sum((x - m) ** 2 for x in w) / len(w)
        out[i] = math.sqrt(var) * math.sqrt(252)
    return out

k, s = bt.broker.load_creds()
start = (dt.date.today() - dt.timedelta(days=6 * 365 + 40)).isoformat()
soxx = bt.fetch("SOXX", start, k, s); soxl = bt.fetch("SOXL", start, k, s); spy = bt.fetch("SPY", start, k, s)
dates, soxx, soxl = bt.align(soxx, soxl)
bl, bh = idx(dates, BEAR[0]), idx(dates, BEAR[1])

# current-live SPY gate
spyd = {b["date"]: b for b in spy}; spc = [spyd[d]["c"] if d in spyd else None for d in dates]
spe = bt.ema_series([c if c is not None else 0 for c in spc], 20)
spy_gate = [(spc[i] is not None and spe[i] is not None and spc[i] > spe[i]) or spc[i] is None for i in range(len(dates))]

vol = realized_vol(soxx, 10)
vals = sorted(v for v in vol if v is not None)
def pct(q): return vals[int(q * len(vals))]
print(f"SOXX 10d realized vol: median {pct(0.5)*100:.0f}%  p70 {pct(0.7)*100:.0f}%  "
      f"p85 {pct(0.85)*100:.0f}%  p95 {pct(0.95)*100:.0f}%\n")

LIVE = dict(chop=True, trail=True, risk_pct=4.0, cost_bps=10)
def run(gate): return bt.simulate(dates, soxx, soxl, bt.cfg(**{**LIVE, "entry_gate": gate}))
def comb(a, b): return [a[i] and b[i] for i in range(len(dates))]
def line(name, m):
    db, sb = sub(m["eq"], bl, bh)
    print(f"{name:34} ret{m['ret']*100:>5.0f}%  DD{m['mdd']*100:>3.0f}%  Sh{m['sharpe']:>4.2f}  trades{m['trades']:>4}  bearSh{sb:>5.2f}")

line("CURRENT LIVE (SPY gate only)", run(spy_gate))
print("\n-- add fear gate: block entries when 10d vol above threshold --")
for q in (0.70, 0.80, 0.85, 0.90, 0.95):
    thr = pct(q)
    fear_ok = [vol[i] is None or vol[i] < thr for i in range(len(dates))]
    line(f"+ fear gate (block > p{int(q*100)}={thr*100:.0f}%)", run(comb(spy_gate, fear_ok)))
print("\n-- fear gate WITHOUT SPY (is it an alternative or redundant?) --")
for q in (0.80, 0.90):
    thr = pct(q)
    fear_ok = [vol[i] is None or vol[i] < thr for i in range(len(dates))]
    line(f"fear-only (block > p{int(q*100)})", run(fear_ok))
