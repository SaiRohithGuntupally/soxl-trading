#!/usr/bin/env python3
"""RQ3 (daily-bar part): entry/exit quality for the trend bots. Entry fill at
next open vs same close, re-entry rules after a trailing-stop exit, ADX
threshold sensitivity (anchored walk-forward), exit on the underlying's EMA
break vs chandelier only vs a deeper SMA50 break, and +-20% perturbation."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

dates, D = C.load_fleet_data()
n = len(dates)
BOTS = [("SOXL", "SOXX", 4.0, True), ("UPRO", "SPY", 2.0, False), ("TNA", "IWM", 2.0, True),
        ("MSTR", "MSTR", 2.0, True), ("PLTR", "PLTR", 2.0, True)]
WIN = [("FULL", "2016-01-04", "2026-12-31"), ("BACK-OOS", C.BACK_LO, C.BACK_HI),
       ("TUNE", C.TUNE_LO, C.TUNE_HI)]


def one(sym, und, rp, mk, **kw):
    return C.simulate_fleet(dates, D, [C.trend_bot(sym, und, rp, mkt_confirm=mk, **kw)],
                            port_break=0, sym_kill=0)


def table(title, variants, first_wf=2019):
    """variants: {label: kwargs}. Prints per-bot FULL / BACK-OOS / TUNE Sharpe+MAR and WF."""
    print("\n#### %s" % title)
    for sym, und, rp, mk in BOTS:
        res = {lab: one(sym, und, rp, mk, **kw) for lab, kw in variants.items()}
        print("\n%s (signal %s)" % (sym, und))
        print("| variant | FULL ret | FULL DD | FULL Sh | FULL MAR | trades | BACK-OOS ret | BACK Sh | BACK MAR | TUNE Sh | TUNE MAR |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for lab, r in res.items():
            f = C.window(r, dates, *WIN[0][1:]); b = C.window(r, dates, *WIN[1][1:]); t = C.window(r, dates, *WIN[2][1:])
            if sym == "PLTR":
                b = dict(ret=0, sharpe=0, mar=0)
            print("| %s | %.0f%% | %.0f%% | %.2f | %.2f | %d | %.0f%% | %.2f | %.2f | %.2f | %.2f |" % (
                lab, f["ret"] * 100, f["mdd"] * 100, f["sharpe"], f["mar"], f["trades"],
                b["ret"] * 100, b["sharpe"], b["mar"], t["sharpe"], t["mar"]))
        fy = 2022 if sym == "PLTR" else first_wf
        m, chosen, _ = C.anchored_wf(dates, res, fy, 2026, key="mar", min_is_days=250)
        lo = C.idx(dates, "%d-01-01" % fy)
        base_lab = list(variants.keys())[0]
        print("WF (select by IS MAR, OOS %d-2026): %s | chosen %s" % (fy, C.fmt(m), [c[1] for c in chosen]))
        print("   same OOS period, first variant '%s' fixed: %s" % (base_lab, C.fmt(C.slice_metrics(res[base_lab], lo, n - 1))))


table("A. entry fill: next open (backtest convention) vs same-day close (15:45 tick)",
      {"next_open (live backtest)": dict(), "close fill": dict(entry_fill="close")})

table("B. re-entry after a stop-out while the trend is still intact",
      {"immediate (live)": dict(), "cooldown 3d": dict(reentry="cooldown:3"),
       "cooldown 5d": dict(reentry="cooldown:5"), "cooldown 10d": dict(reentry="cooldown:10"),
       "fresh EMA cross required": dict(reentry="fresh_cross")})

table("C. ADX threshold", {"adx 25 (live)": dict(adx_min=25.0), "no ADX filter": dict(chop=False),
                           "adx 15": dict(adx_min=15.0), "adx 20": dict(adx_min=20.0),
                           "adx 30": dict(adx_min=30.0), "adx 35": dict(adx_min=35.0)})

table("D. exit rule", {"EMA break + chandelier (live)": dict(),
                       "chandelier only (ignore EMA break)": dict(exit_mode="chand_only"),
                       "EMA break only (no trailing stop)": dict(exit_mode="ema_only", trail=False),
                       "SMA50 break + chandelier": dict(exit_mode="ema_or_sma")})

table("E. EMA length", {"ema 20 (live)": dict(), "ema 10": dict(ema_len=10), "ema 30": dict(ema_len=30),
                        "ema 50": dict(ema_len=50), "ema 100": dict(ema_len=100)})

table("F. chandelier / initial stop", {"ch3 stop1.5 (live)": dict(), "ch2": dict(chand_atr=2.0),
                                      "ch4": dict(chand_atr=4.0), "ch5": dict(chand_atr=5.0),
                                      "stop 2.5": dict(stop_atr=2.5), "stop 1.0": dict(stop_atr=1.0)})

print("\n#### G. +-20% perturbation of the live trend parameters (ema_len, adx_min, stop_atr, chand_atr)")
for sym, und, rp, mk in BOTS:
    base = dict(ema_len=20, adx_min=25.0, stop_atr=1.5, chand_atr=3.0)
    print("\n%s" % sym)
    print("| perturbation | FULL Sh | FULL MAR | BACK-OOS Sh | TUNE Sh |")
    print("|---|---|---|---|---|")
    for lab, cfg in C.perturb(base, ["ema_len", "adx_min", "stop_atr", "chand_atr"]):
        r = one(sym, und, rp, mk, **cfg)
        f = C.window(r, dates, *WIN[0][1:]); b = C.window(r, dates, *WIN[1][1:]); t = C.window(r, dates, *WIN[2][1:])
        print("| %s | %.2f | %.2f | %.2f | %.2f |" % (lab, f["sharpe"], f["mar"], b["sharpe"] if sym != "PLTR" else 0, t["sharpe"]))
