#!/usr/bin/env python3
"""RQ6: validate the candidate package at FLEET level (shared capital, live
rules): close-of-day fill for the trend bots, with and without a core index
holding, with and without the inert mean-reversion bots, plus a whole-package
+-20% perturbation (every trend parameter moved together) to check robustness."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
n = len(dates)
live = C.live_fleet()


def with_close(bots):
    return [dict(b, entry_fill="close") if b["strategy"] == "trend" else b for b in bots]


trend = [b for b in live if b["strategy"] == "trend"]
V = {
    "A live fleet": (live, {}),
    "B live + close fill (trend bots)": (with_close(live), {}),
    "C B minus meanrev bots (5 trend, close fill)": (with_close(trend), {}),
    "D C + 50% SPY core, bots size off non-core": (with_close(trend), dict(core=dict(sym="SPY", frac=0.5), size_on="noncore")),
    "E C + 50% SPY core, bots size off total (live sizing)": (with_close(trend), dict(core=dict(sym="SPY", frac=0.5))),
    "F C + 70% SPY core, size off non-core": (with_close(trend), dict(core=dict(sym="SPY", frac=0.7), size_on="noncore")),
    "G C + 50% QQQ core, size off non-core": (with_close(trend), dict(core=dict(sym="QQQ", frac=0.5), size_on="noncore")),
    "H C + 50% SPY SMA200-timed core, size off non-core": (with_close(trend), dict(core=dict(sym="SPY", frac=0.5, sma=200), size_on="noncore")),
    "I live fleet + 50% SPY core, size off non-core (no close fill)": (live, dict(core=dict(sym="SPY", frac=0.5), size_on="noncore")),
}
res = {k: C.simulate_fleet(dates, D, b, **kw) for k, (b, kw) in V.items()}
res["SPY B&H"] = C.buy_hold(dates, D["SPY"])
res["QQQ B&H"] = C.buy_hold(dates, D["QQQ"])
for lab, lo, hi in C.WINDOWS[:4]:
    print("\n== %s ==" % lab)
    print(C.MD_HDR)
    for k, r in res.items():
        print(C.md_row(k, C.window(r, dates, lo, hi)))

print("\n== yearly ==")
ys = {k: dict(C.yearly(r, dates)) for k, r in res.items()}
keys = ["A live fleet", "B live + close fill (trend bots)", "D C + 50% SPY core, bots size off non-core", "SPY B&H"]
print("| year | " + " | ".join(k.split(" ")[0] if k != "SPY B&H" else "SPY" for k in keys) + " |")
print("|---|" + "---|" * len(keys))
for y in sorted(ys["A live fleet"]):
    print("| %d | " % y + " | ".join("%.1f%%" % (ys[k][y] * 100) for k in keys) + " |")

print("\n== anchored WF across the package variants (select by IS MAR, OOS 2019-2026) ==")
act = {k: v for k, v in res.items() if "B&H" not in k}
m, chosen, _ = C.anchored_wf(dates, act, 2019, 2026, key="mar")
print("  stitched OOS:", C.fmt(m)); print("  chosen:", [(y, c.split(" ")[0]) for y, c in chosen])
lo = C.idx(dates, "2019-01-01")
for k in ("A live fleet", "B live + close fill (trend bots)", "D C + 50% SPY core, bots size off non-core", "SPY B&H"):
    print("  fixed %-50s %s" % (k[:50], C.fmt(C.slice_metrics(res[k], lo, n - 1))))

print("\n== whole-package +-20% perturbation (ema_len, adx_min, stop_atr, chand_atr moved on EVERY trend bot) ==")
for name, bots, kw in (("B close fill", with_close(live), {}),
                       ("D close fill + 50% SPY core (non-core sizing)", with_close(trend), dict(core=dict(sym="SPY", frac=0.5), size_on="noncore"))):
    print("\n%s" % name)
    print("| perturbation | FULL ret | FULL DD | FULL Sh | FULL MAR | BACK-OOS Sh | BACK MAR | TUNE Sh | 2022 ret |")
    print("|---|---|---|---|---|---|---|---|---|")
    base = dict(ema_len=20, adx_min=25.0, stop_atr=1.5, chand_atr=3.0)
    for lab, cfg in C.perturb(base, ["ema_len", "adx_min", "stop_atr", "chand_atr"]):
        bb = [dict(b, **cfg) if b["strategy"] == "trend" else b for b in bots]
        r = C.simulate_fleet(dates, D, bb, **kw)
        f = C.full(r); bk = C.window(r, dates, C.BACK_LO, C.BACK_HI); t = C.window(r, dates, C.TUNE_LO, C.TUNE_HI)
        y22 = C.window(r, dates, "2022-01-01", "2022-12-31")
        print("| %s | %.0f%% | %.0f%% | %.2f | %.2f | %.2f | %.2f | %.2f | %.0f%% |" % (
            lab, f["ret"] * 100, f["mdd"] * 100, f["sharpe"], f["mar"], bk["sharpe"], bk["mar"], t["sharpe"], y22["ret"] * 100))

print("\n== per-bot standalone with close fill: trade count and same-day stop-outs are unchanged by construction; show FULL/BACK/TUNE for the record ==")
for b in trend:
    r0 = C.simulate_fleet(dates, D, [b], port_break=0, sym_kill=0)
    r1 = C.simulate_fleet(dates, D, [dict(b, entry_fill="close")], port_break=0, sym_kill=0)
    for lab, r in (("next_open", r0), ("close", r1)):
        f = C.full(r); bk = C.window(r, dates, C.BACK_LO, C.BACK_HI); t = C.window(r, dates, C.TUNE_LO, C.TUNE_HI)
        print("  %-5s %-9s FULL Sh %.2f MAR %.2f DD %.0f%% | BACK Sh %.2f | TUNE Sh %.2f | trades %d" % (
            b["name"], lab, f["sharpe"], f["mar"], f["mdd"] * 100, bk["sharpe"], t["sharpe"], f["trades"]))
