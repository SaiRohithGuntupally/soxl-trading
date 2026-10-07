#!/usr/bin/env python3
"""RQ5: single highest-leverage ideas. (a) portfolio-level ex-ante vol cap,
(b) market-vol scaling of all risk, (c) trade the unlevered underlying with
sized exposure instead of the 3x ETF, (d) FOMC event gating vs holding through,
(e) what the 2x paper buying power does to the fleet."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
n = len(dates)
fleet = C.live_fleet()
WIN = C.WINDOWS[:4]


def show(title, res):
    for lab, lo, hi in WIN:
        print("\n== %s :: %s ==" % (title, lab))
        print(C.MD_HDR)
        for k, r in res.items():
            print(C.md_row(k, C.window(r, dates, lo, hi)))


# (a) ex-ante vol cap across the fleet (correlation assumed 1 = conservative)
res = {"fleet (live)": C.simulate_fleet(dates, D, fleet)}
for vc in (0.10, 0.15, 0.20, 0.30, 0.40):
    res["fleet, ex-ante vol cap %.0f%%" % (vc * 100)] = C.simulate_fleet(dates, D, fleet, vol_cap=vc)
show("(a) portfolio ex-ante vol cap", res)

# (b) market-vol scaling of every bot's risk: scale = clamp(target / SPY 20d realized vol)
rv = C.realized_vol(D["SPY"], 20)
res = {"fleet (live)": C.simulate_fleet(dates, D, fleet)}
for tgt, cap in ((0.15, 1.0), (0.20, 1.0), (0.15, 1.5), (0.20, 1.5)):
    sc = [1.0 if v is None or v <= 0 else max(0.25, min(cap, tgt / v)) for v in rv]
    res["risk x clamp(%.0f%%/SPYvol, 0.25..%.1f)" % (tgt * 100, cap)] = C.simulate_fleet(dates, D, fleet, port_scale=sc)
show("(b) market-vol scaling of risk", res)

# (c) underlying with sized exposure instead of the 3x ETF
print("\n\n#### (c) trade the UNDERLYING instead of the 3x ETF (same signals, same risk-based sizing; margin 7%/yr on negative cash)")
for sym, und, rp, mk in (("SOXL", "SOXX", 4.0, True), ("UPRO", "SPY", 2.0, False), ("TNA", "IWM", 2.0, True),
                         ("TQQQ", "QQQ", 2.0, False)):
    strat = "meanrev" if sym == "TQQQ" else "trend"
    base = C.meanrev_bot(sym, und, rp) if strat == "meanrev" else C.trend_bot(sym, und, rp, mkt_confirm=mk)
    res = {}
    res["%s 3x ETF (live)" % sym] = C.simulate_fleet(dates, D, [base], port_break=0, sym_kill=0)
    res["%s underlying 1x, same risk" % und] = C.simulate_fleet(dates, D, [dict(base, sym=und, lev=1.0)], port_break=0, sym_kill=0)
    res["%s underlying, 2x notional (Reg T)" % und] = C.simulate_fleet(dates, D, [dict(base, sym=und, lev=2.0)], port_break=0, sym_kill=0, max_gross=2.0)
    res["%s underlying, 3x notional (needs portfolio margin)" % und] = C.simulate_fleet(dates, D, [dict(base, sym=und, lev=3.0)], port_break=0, sym_kill=0, max_gross=3.0)
    show("(c) %s" % sym, res)

# (d) FOMC event gating: block entries the day before and the day of
FOMC = """2016-01-27 2016-03-16 2016-04-27 2016-06-15 2016-07-27 2016-09-21 2016-11-02 2016-12-14
2017-02-01 2017-03-15 2017-05-03 2017-06-14 2017-07-26 2017-09-20 2017-11-01 2017-12-13
2018-01-31 2018-03-21 2018-05-02 2018-06-13 2018-08-01 2018-09-26 2018-11-08 2018-12-19
2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11
2020-01-29 2020-03-03 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16 2026-10-28 2026-12-09""".split()
ev = set(FOMC)
gate1 = [True] * n; gate3 = [True] * n
for i, d in enumerate(dates):
    for k in range(0, 2):
        if i + k < n and dates[i + k] in ev:
            gate1[i] = False
    for k in range(0, 4):
        if i + k < n and dates[i + k] in ev:
            gate3[i] = False
print("\n\n#### (d) FOMC gating (CPI/NVDA dates not reconstructed; FOMC is the dominant scheduled event)")
res = {"fleet, no event gate": C.simulate_fleet(dates, D, fleet),
       "fleet, block entries 1d before + FOMC day (live rule)": C.simulate_fleet(dates, D, [dict(b, gate=gate1) for b in fleet]),
       "fleet, block entries 3d before": C.simulate_fleet(dates, D, [dict(b, gate=gate3) for b in fleet])}
show("(d) event gating", res)
print("  entry days blocked by 1d rule: %d of %d" % (sum(1 for g in gate1 if not g), n))

# (e) 2x buying power (what the paper account permits) + vol cap under it
print("\n\n#### (e) the paper account allows 2x buying power; the fleet is NOT capped at 100% gross live")
res = {"fleet, gross <= 1x": C.simulate_fleet(dates, D, fleet, max_gross=1.0),
       "fleet, gross <= 2x (live reality)": C.simulate_fleet(dates, D, fleet, max_gross=2.0),
       "fleet 2x + vol cap 20%": C.simulate_fleet(dates, D, fleet, max_gross=2.0, vol_cap=0.20),
       "fleet 2x + vol cap 30%": C.simulate_fleet(dates, D, fleet, max_gross=2.0, vol_cap=0.30)}
show("(e) buying power", res)
for k, r in res.items():
    print("  %-35s breaker %d kill %d" % (k, len(r["breaker_days"]), len(r["kill_days"])))
