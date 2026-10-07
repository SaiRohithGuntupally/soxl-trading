#!/usr/bin/env python3
"""RQ2: portfolio-level allocation. Fleet subsets, fewer bots at higher
conviction, walk-forward risk parity, and whether the 15% breaker / 10% kill
switch ever fire (with and without the 2x margin the paper account allows)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
n = len(dates)
T = C.trend_bot; M = C.meanrev_bot

subsets = {
    "live fleet (7)": C.live_fleet(),
    "trend-only (5)": [b for b in C.live_fleet() if b["strategy"] == "trend"],
    "SOXL only 4%": [T("SOXL", "SOXX", 4.0)],
    "UPRO only 2%": [T("UPRO", "SPY", 2.0, mkt_confirm=False)],
    "UPRO only 4%": [T("UPRO", "SPY", 4.0, mkt_confirm=False)],
    "SOXL+UPRO (4%,2%)": [T("SOXL", "SOXX", 4.0), T("UPRO", "SPY", 2.0, mkt_confirm=False)],
    "SOXL+UPRO (4%,4%)": [T("SOXL", "SOXX", 4.0), T("UPRO", "SPY", 4.0, mkt_confirm=False)],
    "ETFs only (SOXL,TNA,UPRO,TQQQ,LABU)": [b for b in C.live_fleet() if b["name"] not in ("MSTR", "PLTR")],
    "no SOXL (6)": [b for b in C.live_fleet() if b["name"] != "SOXL"],
    "all 7 at 2%": [dict(b, risk_pct=2.0) for b in C.live_fleet()],
    "all 7 at 4% (ceiling)": [dict(b, risk_pct=4.0) for b in C.live_fleet()],
    "trend 5 at 4%": [dict(b, risk_pct=4.0) for b in C.live_fleet() if b["strategy"] == "trend"],
}
res = {k: C.simulate_fleet(dates, D, v) for k, v in subsets.items()}
res["SPY B&H"] = C.buy_hold(dates, D["SPY"])
for lab, lo, hi in C.WINDOWS[:4]:
    print("\n== %s ==" % lab)
    print(C.MD_HDR)
    for k, r in res.items():
        print(C.md_row(k, C.window(r, dates, lo, hi)))

print("\n== anchored walk-forward over subsets (select by IS MAR each year, OOS 2019-2026) ==")
act = {k: v for k, v in res.items() if k != "SPY B&H"}
m, chosen, _ = C.anchored_wf(dates, act, 2019, 2026, key="mar")
print("  stitched OOS:", C.fmt(m))
print("  chosen:", chosen)
m2, chosen2, _ = C.anchored_wf(dates, act, 2019, 2026, key="sharpe")
print("  stitched OOS (select by Sharpe):", C.fmt(m2)); print("  chosen:", chosen2)
lo = C.idx(dates, "2019-01-01")
print("  reference, same OOS period: live fleet", C.fmt(C.slice_metrics(res["live fleet (7)"], lo, n - 1)))
print("  reference, same OOS period: SPY B&H  ", C.fmt(C.slice_metrics(res["SPY B&H"], lo, n - 1)))

print("\n== walk-forward RISK PARITY (risk_pct ~ 1/standalone vol, measured on data before each year; total risk budget held at the live 16 points) ==")
fleet = C.live_fleet()
standalone = {b["name"]: C.simulate_fleet(dates, D, [dict(b, risk_pct=2.0)], port_break=0, sym_kill=0) for b in fleet}
eq = [100000.0]; weights_log = []
for y in range(2018, 2027):
    lo, hi = C.year_bounds(dates, y)
    vols = {}
    for nm, r in standalone.items():
        mm = C.slice_metrics(r, 0, lo - 1)
        vols[nm] = max(mm["vol"], 0.01)
    inv = {nm: 1.0 / v for nm, v in vols.items()}
    tot = sum(inv.values())
    risks = {nm: 16.0 * inv[nm] / tot for nm in inv}
    weights_log.append((y, {k: round(v, 1) for k, v in risks.items()}))
    bots = [dict(b, risk_pct=min(4.0, risks[b["name"]])) for b in fleet]   # respect the 4% ceiling
    r = C.simulate_fleet(dates, D, bots)
    for i in range(lo, hi + 1):
        eq.append(eq[-1] * r["eq"][i] / r["eq"][i - 1])
lo18 = C.idx(dates, "2018-01-01")
print("  risk-parity fleet 2018-2026:", C.fmt(C.metrics(eq)))
print("  live fleet        2018-2026:", C.fmt(C.slice_metrics(res["live fleet (7)"], lo18, n - 1)))
print("  SPY B&H           2018-2026:", C.fmt(C.slice_metrics(res["SPY B&H"], lo18, n - 1)))
for y, w in weights_log:
    print("   ", y, w)

print("\n== breakers: does the 15% portfolio breaker / 10% kill ever fire? ==")
for gross in (1.0, 2.0):
    for pb, sk in ((0.15, 0.10), (0.10, 0.10), (0.05, 0.05), (0, 0)):
        r = C.simulate_fleet(dates, D, C.live_fleet(), max_gross=gross, port_break=pb, sym_kill=sk)
        print("  max_gross %.0fx  port_break %4.2f  sym_kill %4.2f : %s | breaker fired %d %s | kill fired %d %s" % (
            gross, pb, sk, C.fmt(C.full(r)), len(r["breaker_days"]), r["breaker_days"][:6],
            len(r["kill_days"]), r["kill_days"][:6]))
print("\n== what the live account actually permits: 2x buying power, gross exposure stats ==")
r2 = C.simulate_fleet(dates, D, C.live_fleet(), max_gross=2.0)
g = sorted(r2["gross"])
print("  2x: avg gross %.0f%%  p90 %.0f%%  max %.0f%%" % (100 * sum(g) / n, 100 * g[int(0.9 * n)], 100 * g[-1]))
g1 = sorted(res["live fleet (7)"]["gross"])
print("  1x: avg gross %.0f%%  p90 %.0f%%  max %.0f%%" % (100 * sum(g1) / n, 100 * g1[int(0.9 * n)], 100 * g1[-1]))
