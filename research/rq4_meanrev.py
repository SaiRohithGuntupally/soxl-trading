#!/usr/bin/env python3
"""RQ4: the mean-reversion bots (TQQQ/QQQ, LABU/XBI). RSI14<30 inside
price>SMA200 almost never fires. Compare 2-day RSI and pullback-to-EMA entries,
regime filter on/off, live exit mechanics (2 ATR stop, 2R target, no trail) vs
analyze.py's chandelier version, with anchored walk-forward and perturbation."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
n = len(dates)
PAIRS = [("TQQQ", "QQQ"), ("LABU", "XBI"), ("UPRO", "SPY")]
WIN = [("FULL", "2016-01-04", "2026-12-31"), ("BACK-OOS", C.BACK_LO, C.BACK_HI), ("TUNE", C.TUNE_LO, C.TUNE_HI)]

V = {
    "rsi14<30 >55 regime200 (live)": dict(),
    "rsi14<30 no regime": dict(regime_ma=0),
    "rsi14<30 regime, chandelier exit (analyze.py)": dict(trail=True),
    "rsi14<35 regime": dict(rsi_buy=35.0),
    "rsi2<10 exit rsi2>65|>SMA5, regime200": dict(mr_variant="rsi2", rsi_len=2, rsi_buy=10.0, rsi_sell=65.0),
    "rsi2<10 no regime": dict(mr_variant="rsi2", rsi_len=2, rsi_buy=10.0, rsi_sell=65.0, regime_ma=0),
    "rsi2<5 regime200": dict(mr_variant="rsi2", rsi_len=2, rsi_buy=5.0, rsi_sell=65.0),
    "rsi2<10 regime200, max hold 10d": dict(mr_variant="rsi2", rsi_len=2, rsi_buy=10.0, rsi_sell=65.0, max_hold=10),
    "pullback close<EMA20 in regime200": dict(mr_variant="pullback"),
    "pullback close<EMA20*0.97 in regime200": dict(mr_variant="pullback", pb_pct=0.03),
    "pullback no regime": dict(mr_variant="pullback", regime_ma=0),
}
for sym, und in PAIRS:
    res = {lab: C.simulate_fleet(dates, D, [C.meanrev_bot(sym, und, 2.0, **kw)], port_break=0, sym_kill=0)
           for lab, kw in V.items()}
    print("\n### %s (signal %s), risk 2%%" % (sym, und))
    print("| variant | FULL ret | FULL DD | FULL Sh | FULL MAR | trades | win | BACK-OOS ret | BACK Sh | BACK MAR | TUNE ret | TUNE Sh | TUNE MAR |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, r in res.items():
        f = C.window(r, dates, *WIN[0][1:]); b = C.window(r, dates, *WIN[1][1:]); t = C.window(r, dates, *WIN[2][1:])
        print("| %s | %.0f%% | %.1f%% | %.2f | %.2f | %d | %.0f%% | %.0f%% | %.2f | %.2f | %.0f%% | %.2f | %.2f |" % (
            lab, f["ret"] * 100, f["mdd"] * 100, f["sharpe"], f["mar"], f["trades"], f["win"] * 100,
            b["ret"] * 100, b["sharpe"], b["mar"], t["ret"] * 100, t["sharpe"], t["mar"]))
    m, chosen, _ = C.anchored_wf(dates, res, 2019, 2026, key="mar")
    lo = C.idx(dates, "2019-01-01")
    print("WF (select by IS MAR, OOS 2019-2026): %s" % C.fmt(m)); print("   chosen:", [c[1] for c in chosen])
    print("   live variant fixed, same period: %s" % C.fmt(C.slice_metrics(res["rsi14<30 >55 regime200 (live)"], lo, n - 1)))
    print("   B&H %s same period: %s" % (und, C.fmt(C.slice_metrics(C.buy_hold(dates, D[und]), lo, n - 1))))

print("\n### +-20% perturbation of the rsi2 variant (rsi_buy, rsi_sell, stop_atr) and of live (rsi_buy, rsi_sell, stop_atr)")
for sym, und in PAIRS[:2]:
    for name, base in (("rsi2", dict(mr_variant="rsi2", rsi_len=2, rsi_buy=10.0, rsi_sell=65.0, stop_atr=2.0)),
                       ("live rsi14", dict(rsi_buy=30.0, rsi_sell=55.0, stop_atr=2.0))):
        print("\n%s %s" % (sym, name))
        print("| perturbation | FULL Sh | FULL MAR | trades | BACK-OOS Sh | TUNE Sh |")
        print("|---|---|---|---|---|---|")
        for lab, cfg in C.perturb(base, ["rsi_buy", "rsi_sell", "stop_atr"]):
            r = C.simulate_fleet(dates, D, [C.meanrev_bot(sym, und, 2.0, **cfg)], port_break=0, sym_kill=0)
            f = C.window(r, dates, *WIN[0][1:]); b = C.window(r, dates, *WIN[1][1:]); t = C.window(r, dates, *WIN[2][1:])
            print("| %s | %.2f | %.2f | %d | %.2f | %.2f |" % (lab, f["sharpe"], f["mar"], f["trades"], b["sharpe"], t["sharpe"]))

print("\n### how often does the live entry fire? days with entry condition true")
for sym, und in PAIRS:
    for lab in ("rsi14<30 >55 regime200 (live)", "rsi14<30 no regime", "rsi2<10 exit rsi2>65|>SMA5, regime200", "pullback close<EMA20 in regime200"):
        b = C.meanrev_bot(sym, und, 2.0, **V[lab])
        pc = C.precompute(b, D, {})
        print("  %-5s %-45s entry-true days %4d of %d" % (sym, lab, sum(pc["ent"]), n))
