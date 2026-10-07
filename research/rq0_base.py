#!/usr/bin/env python3
"""RQ0: the honest base case. Each deployed bot and the whole live fleet (shared
capital, 50% cap, breakers) on SIP bars 2016-2026, split into the window the
parameters were tuned on vs the 2016-2020 window no prior research ever saw,
vs buy-and-hold of the ETF, the underlying, SPY and QQQ."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
n = len(dates)
print("Data: SIP daily %s..%s (%d days). PLTR from 2020-09-30." % (dates[0], dates[-1], n))

fleet = C.live_fleet()
print("\n== PER-BOT standalone (own 100k, no fleet rules) vs buy & hold ==")
per = {}
for b in fleet:
    res = C.simulate_fleet(dates, D, [b], port_break=0, sym_kill=0)
    per[b["name"]] = res
    bh = C.buy_hold(dates, D[b["sym"]])
    bhu = C.buy_hold(dates, D[b["und"]])
    print("\n-- %s (signal %s, %s, risk %.0f%%)" % (b["name"], b["und"], b["strategy"], b["risk_pct"]))
    for lab, lo, hi in C.WINDOWS:
        if b["name"] == "PLTR" and lo < "2020-09-30":
            continue
        m = C.window(res, dates, lo, hi); mb = C.window(bh, dates, lo, hi); mu = C.window(bhu, dates, lo, hi)
        print("  %-30s bot  %s" % (lab, C.fmt(m)))
        print("  %-30s B&H %s: ret %5.0f%% DD %3.0f%% Sh %5.2f MAR %5.2f | B&H %s: ret %5.0f%% DD %3.0f%% Sh %5.2f MAR %5.2f" % (
            "", b["sym"], mb["ret"] * 100, mb["mdd"] * 100, mb["sharpe"], mb["mar"],
            b["und"], mu["ret"] * 100, mu["mdd"] * 100, mu["sharpe"], mu["mar"]))

print("\n== LIVE FLEET (7 bots, shared 100k, 50%% cap each, 15%% breaker, 10%% kill, no margin) ==")
fl = C.simulate_fleet(dates, D, fleet)
spy = C.buy_hold(dates, D["SPY"]); qqq = C.buy_hold(dates, D["QQQ"]); soxl = C.buy_hold(dates, D["SOXL"])
for lab, lo, hi in C.WINDOWS:
    print("  %-30s fleet %s" % (lab, C.fmt(C.window(fl, dates, lo, hi))))
    for nm, r in (("SPY", spy), ("QQQ", qqq), ("SOXL", soxl)):
        m = C.window(r, dates, lo, hi)
        print("  %-30s  B&H %-4s ret %5.0f%% CAGR %5.1f%% DD %3.0f%% Sh %5.2f MAR %5.2f" % (
            "", nm, m["ret"] * 100, m["cagr"] * 100, m["mdd"] * 100, m["sharpe"], m["mar"]))
print("  breaker days:", fl["breaker_days"]); print("  kill days:", fl["kill_days"])
print("  avg gross exposure %.0f%%, days with any position %.0f%%" % (
    100 * sum(fl["gross"]) / n, 100 * sum(1 for g in fl["gross"] if g > 0) / n))

print("\n== YEARLY returns: fleet vs SPY vs QQQ vs SOXL ==")
fy = dict(C.yearly(fl, dates)); sy = dict(C.yearly(spy, dates)); qy = dict(C.yearly(qqq, dates)); ly = dict(C.yearly(soxl, dates))
print("  year   fleet    SPY    QQQ    SOXL")
for y in sorted(fy):
    print("  %d  %6.1f%% %6.1f%% %6.1f%% %6.1f%%" % (y, fy[y] * 100, sy[y] * 100, qy[y] * 100, ly[y] * 100))

print("\n== per-bot contribution inside the fleet (sum of trade returns weighted is not additive; report trade counts and win rate) ==")
for nm, tr in fl["per_bot"].items():
    w = [t for t in tr if t > 0]
    print("  %-5s trades %3d  win %3.0f%%  avg %+5.1f%%  sum %+6.0f%%" % (
        nm, len(tr), 100 * len(w) / len(tr) if tr else 0, 100 * (sum(tr) / len(tr) if tr else 0), 100 * sum(tr)))
