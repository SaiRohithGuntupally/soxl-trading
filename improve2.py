#!/usr/bin/env python3
"""Round-2 research: do costs kill the edge? do broad-market confirmation (SPY) or
ADX-dynamic sizing add GENUINE value (Sharpe), not just leverage?"""
import math, datetime as dt
import backtest as bt

BEAR=("2021-12-01","2022-12-31")

def sub(eq, lo, hi):
    seg=eq[lo:hi+1]; cap=seg[0]; peak=-1e18; mdd=0
    for v in seg:
        peak=max(peak,v); mdd=max(mdd,(peak-v)/peak if peak>0 else 0)
    r=[seg[i]/seg[i-1]-1 for i in range(1,len(seg)) if seg[i-1]>0]
    m=sum(r)/len(r) if r else 0; var=sum((x-m)**2 for x in r)/len(r) if r else 0
    return mdd, (m/math.sqrt(var)*math.sqrt(252) if var>0 else 0)

def idx(dates,t):
    for i,d in enumerate(dates):
        if d>=t: return i
    return len(dates)-1

k,s=bt.broker.load_creds()
start=(dt.date.today()-dt.timedelta(days=6*365+40)).isoformat()
soxx=bt.fetch("SOXX",start,k,s); soxl=bt.fetch("SOXL",start,k,s); spy=bt.fetch("SPY",start,k,s)
dates,soxx,soxl=bt.align(soxx,soxl)
bl,bh=idx(dates,BEAR[0]),idx(dates,BEAR[1])

# SPY broad-market gate: SPY close > its EMA20, aligned to our dates
spyd={b["date"]:b for b in spy}
spc=[spyd[d]["c"] if d in spyd else None for d in dates]
spe=bt.ema_series([c if c is not None else 0 for c in spc],20)
spy_gate=[(spc[i] is not None and spe[i] is not None and spc[i]>spe[i]) or (spc[i] is None) for i in range(len(dates))]

# ADX-dynamic size scale (bigger in strong trends)
adx=bt.adx_series(soxx,14)
def scale(a): return 1.0 if a is None else max(0.5,min(1.5,0.5+(a-15)/20))
size_scale=[scale(adx[i]) for i in range(len(dates))]

BASE=dict(chop=True,trail=True,risk_pct=4.0,ema_len=20,adx_min=25.0,stop_atr=1.5,chand_atr=3.0)
def run(**o): return bt.simulate(dates,soxx,soxl,bt.cfg(**{**BASE,**o}))
def line(name,m):
    db,sb=sub(m["eq"],bl,bh)
    print(f"{name:30} ret{m['ret']*100:>5.0f}%  DD{m['mdd']*100:>3.0f}%  Sh{m['sharpe']:>4.2f}  trades{m['trades']:>4}  | bearSh{sb:>5.2f}")

print("== Q1: does the edge survive transaction costs? (current config) ==")
for c in (0,5,10,20,40):
    line(f"cost {c}bps/side", run(cost_bps=c))
print("\n== Q2/Q3: new signal ideas (all at 10bps cost, vs that baseline) ==")
line("baseline (10bps)", run(cost_bps=10))
line("+ SPY mkt confirm", run(cost_bps=10, entry_gate=spy_gate))
line("+ ADX-dynamic sizing", run(cost_bps=10, size_scale=size_scale))
line("+ both", run(cost_bps=10, entry_gate=spy_gate, size_scale=size_scale))

print("\n== Q4: is the SPY filter robust across EMA lengths? (plateau check, 10bps) ==")
for n in (20, 50, 100, 150):
    spe_n = bt.ema_series([c if c is not None else 0 for c in spc], n)
    gate_n = [(spc[i] is not None and spe_n[i] is not None and spc[i] > spe_n[i])
              or (spc[i] is None) for i in range(len(dates))]
    line(f"SPY > EMA{n}", run(cost_bps=10, entry_gate=gate_n))
