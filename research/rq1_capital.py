#!/usr/bin/env python3
"""RQ1: capital efficiency. Is idle cash the biggest drag? Fleet vs fleet + core
holding (SPY/QQQ buy-hold or SMA200-timed) vs 100% buy-hold. Bots keep sizing
off TOTAL equity (as live) but can only spend non-core cash."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
fleet = C.live_fleet()

variants = [("fleet (live)", dict()),
            ("fleet + 30% SPY core", dict(core=dict(sym="SPY", frac=0.30))),
            ("fleet + 50% SPY core", dict(core=dict(sym="SPY", frac=0.50))),
            ("fleet + 70% SPY core", dict(core=dict(sym="SPY", frac=0.70))),
            ("fleet + 50% QQQ core", dict(core=dict(sym="QQQ", frac=0.50))),
            ("fleet + 50% SPY SMA200-timed", dict(core=dict(sym="SPY", frac=0.50, sma=200))),
            ("fleet + 70% SPY SMA200-timed", dict(core=dict(sym="SPY", frac=0.70, sma=200))),
            ("fleet + 50% QQQ SMA200-timed", dict(core=dict(sym="QQQ", frac=0.50, sma=200))),
            ("fleet + 50% SPY core, bots size off non-core", dict(core=dict(sym="SPY", frac=0.50), size_on="noncore")),
            ]
results = {}
for lab, kw in variants:
    results[lab] = C.simulate_fleet(dates, D, fleet, **kw)
# pure benchmarks
results["100% SPY B&H"] = C.buy_hold(dates, D["SPY"])
results["100% QQQ B&H"] = C.buy_hold(dates, D["QQQ"])
results["100% SOXL B&H"] = C.buy_hold(dates, D["SOXL"])
# SMA200-timed SPY alone (no bots)
results["100% SPY SMA200-timed"] = C.simulate_fleet(dates, D, [], core=dict(sym="SPY", frac=1.0, sma=200))
results["100% QQQ SMA200-timed"] = C.simulate_fleet(dates, D, [], core=dict(sym="QQQ", frac=1.0, sma=200))
# 60/40-style simple blends of curves (daily rebalanced) for reference
fl = results["fleet (live)"]["eq"]; sp = results["100% SPY B&H"]["eq"]
bl = C.blend([(fl, 0.5), (sp, 0.5)])
results["50/50 blend fleet/SPY (daily rebal)"] = dict(eq=bl, gross=[0.0] * len(bl), trade_log=[], turn_log=[])

for lab, lo, hi in C.WINDOWS:
    print("\n== %s ==" % lab)
    print(C.MD_HDR)
    for k, r in results.items():
        print(C.md_row(k, C.window(r, dates, lo, hi)))
print("\nbreaker days with 50% SPY core:", results["fleet + 50% SPY core"]["breaker_days"])
print("\n== yearly: fleet vs fleet+50%SPY vs fleet+50%SPY-timed vs SPY ==")
a = dict(C.yearly(results["fleet (live)"], dates)); b = dict(C.yearly(results["fleet + 50% SPY core"], dates))
c = dict(C.yearly(results["fleet + 50% SPY SMA200-timed"], dates)); s = dict(C.yearly(results["100% SPY B&H"], dates))
for y in sorted(a):
    print("  %d  fleet %6.1f%%  +core %6.1f%%  +timed %6.1f%%  SPY %6.1f%%" % (y, a[y]*100, b[y]*100, c[y]*100, s[y]*100))
