#!/usr/bin/env python3
"""
Reproduce the validated numbers for the v2 config using the research fleet engine
(research/common.py, SIP daily bars 2016-2026, shared capital, 10 bps per side).
This is the contract between the config and the evidence: if you change config.json,
rerun this and compare against research/PROFIT-REVIEW-2026-10-06.md variant D
(FULL 16.1% CAGR / 30.2% maxDD / Sharpe 0.91 / MAR 0.53; BACK-OOS Sharpe 0.63; TUNE 1.08).

  python3 backtest.py            # v2 config vs v1 live fleet vs SPY buy-and-hold
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research"))
import engine  # noqa: E402
import common as C  # noqa: E402


def v2_bots(cfg):
    out = []
    for b in cfg["bots"]:
        out.append(C.trend_bot(b["symbol"], b["underlying"], risk_pct=float(b["risk_pct"]),
                               ema_len=int(b["ema_len"]), adx_min=float(b["adx_min"]), chop=bool(b.get("chop_filter", True)),
                               stop_atr=float(b["stop_atr"]), chand_atr=float(b["chand_atr"]), trail=bool(b.get("trailing", True)),
                               tp_R=float(b.get("trail_tp_R", 8.0)) if b.get("trailing", True) else float(b.get("tp_R", 2.0)),
                               atr_len=int(b["atr_len"]), max_pos_pct=float(cfg["account"]["max_position_pct"]),
                               mkt_confirm=bool(b.get("mkt_confirm", True)), mkt_ema=int(b.get("mkt_ema", 20)),
                               entry_fill="close"))     # decisions on the near-complete bar, filled at that price
    return out


def main() -> int:
    cfg = engine.load_config()
    dates, D = C.load_fleet_data()
    n = len(dates)
    acct = cfg["account"]; core = cfg["core"]
    kw = dict(max_gross=acct["max_gross_pct"] / 100.0, port_break=acct["portfolio_max_loss_pct"] / 100.0,
              sym_kill=acct["max_daily_loss_pct"] / 100.0)
    if core["fraction"] > 0:
        kw.update(core=dict(sym=core["symbol"], frac=core["fraction"]), size_on="noncore")
    runs = {
        "v2 (this config)": C.simulate_fleet(dates, D, v2_bots(cfg), **kw),
        "v1 live fleet (7 bots, no core)": C.simulate_fleet(dates, D, C.live_fleet()),
    }
    spy = C.buy_hold(dates, D["SPY"])
    print(f"bars {dates[0]} .. {dates[-1]} ({n} sessions); costs {C.COST_BPS} bps/side; capital $100k")
    print(f"{'variant':34} {'window':9} {'CAGR':>6} {'maxDD':>6} {'Sharpe':>6} {'MAR':>5}")
    windows = [("FULL", 0, n - 1), ("BACK-OOS", C.idx(dates, C.BACK_LO), C.idx(dates, C.BACK_HI)),
               ("TUNE", C.idx(dates, C.TUNE_LO), C.idx(dates, C.TUNE_HI))]
    for name, r in list(runs.items()) + [("SPY buy-and-hold", spy)]:
        for w, lo, hi in windows:
            m = C.slice_metrics(r, lo, hi)
            print(f"{name:34} {w:9} {m['cagr']*100:6.1f} {m['mdd']*100:6.1f} {m['sharpe']:6.2f} {m['mar']:5.2f}")
        print()
    y = dict(C.yearly(runs["v2 (this config)"], dates)); ys = dict(C.yearly(spy, dates)); yv1 = dict(C.yearly(runs["v1 live fleet (7 bots, no core)"], dates))
    print("year   v2      v1     SPY")
    for yr in sorted(y):
        print(f"{yr}  {y[yr]*100:6.1f}% {yv1.get(yr,0)*100:6.1f}% {ys.get(yr,0)*100:6.1f}%")
    print("\nRead research/PROFIT-REVIEW-2026-10-06.md section 5 (caveats) before believing any of this: "
          "Sharpe CI is roughly +-0.6 on 10 years; the core is market beta; the sleeve has no demonstrated OOS edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
