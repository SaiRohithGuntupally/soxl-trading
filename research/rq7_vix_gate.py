#!/usr/bin/env python3
"""
RQ7: does a VIX-based risk-off gate on NEW ENTRIES improve the v2 fleet out of sample?

Gates use CBOE VIX / VIX3M closes (research/.cache/cboe/*.csv, free CBOE history) with
information through close[i], the same timing as the bots' own signals. Entry-only:
positions already open are managed by their stops and trend exits as usual.

Variants: absolute VIX level, VIX vs its own moving average, VIX percentile,
term structure (VIX/VIX3M, backwardation = stress), and combos. Adopt only if Sharpe
AND MAR improve in BOTH the BACK-OOS (2016-2020, never tuned on) and TUNE windows and
the result survives +-20% on the threshold.
"""
from __future__ import annotations

import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import common as C  # noqa: E402

spec = importlib.util.spec_from_file_location("v2bt", os.path.join(ROOT, "v2", "backtest.py"))
v2bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(v2bt)
import engine as v2engine  # noqa: E402  (v2/ is on sys.path via v2bt)


def load_cboe(name):
    out = {}
    with open(os.path.join(HERE, ".cache", "cboe", name), newline="") as fh:
        for row in csv.DictReader(fh):
            m, d, y = row["DATE"].split("/")
            out[f"{y}-{m}-{d}"] = float(row["CLOSE"])
    return out


def align(series, dates):
    """Forward-fill a {date: value} series onto the fleet's trading dates."""
    keys = sorted(series)
    out, j, last = [], 0, None
    for d in dates:
        while j < len(keys) and keys[j] <= d:
            last = series[keys[j]]; j += 1
        out.append(last)
    return out


def sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def pctile(xs, n):
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        w = xs[i - n + 1:i + 1]
        out[i] = sum(1 for v in w if v <= xs[i]) / len(w)
    return out


def main():
    cfg = v2engine.load_config()
    dates, D = C.load_fleet_data()
    n = len(dates)
    vix = align(load_cboe("VIX_History.csv"), dates)
    v3m = align(load_cboe("VIX3M_History.csv"), dates)
    ratio = [a / b if (a and b) else None for a, b in zip(vix, v3m)]
    vsma50 = sma(vix, 50); vsma20 = sma(vix, 20); vp252 = pctile(vix, 252)

    def g_level(t): return [v is not None and v < t for v in vix]
    def g_sma(k): s = vsma50 if k == 50 else vsma20; return [v is not None and s[i] is not None and v < s[i] for i, v in enumerate(vix)]
    def g_ratio(t): return [r is not None and r < t for r in ratio]
    def g_pct(t): return [p is not None and p < t for p in vp252]
    def g_and(a, b): return [x and y for x, y in zip(a, b)]
    def g_or(a, b): return [x or y for x, y in zip(a, b)]

    gates = {
        "none (v2 as deployed)": None,
        "VIX < 20": g_level(20), "VIX < 25": g_level(25), "VIX < 30": g_level(30), "VIX < 35": g_level(35),
        "VIX < SMA50(VIX)": g_sma(50), "VIX < SMA20(VIX)": g_sma(20),
        "VIX < SMA50 or VIX < 20": g_or(g_sma(50), g_level(20)),
        "VIX 1y percentile < 80%": g_pct(0.8), "VIX 1y percentile < 90%": g_pct(0.9),
        "contango VIX/VIX3M < 1.00": g_ratio(1.0), "VIX/VIX3M < 0.95": g_ratio(0.95), "VIX/VIX3M < 1.05": g_ratio(1.05),
        "contango and VIX < 30": g_and(g_ratio(1.0), g_level(30)),
        "contango and VIX < SMA50": g_and(g_ratio(1.0), g_sma(50)),
    }
    acct = cfg["account"]; core = cfg["core"]
    kw = dict(max_gross=acct["max_gross_pct"] / 100.0, port_break=acct["portfolio_max_loss_pct"] / 100.0,
              sym_kill=acct["max_daily_loss_pct"] / 100.0, core=dict(sym=core["symbol"], frac=core["fraction"]), size_on="noncore")
    bots0 = v2bt.v2_bots(cfg)
    wins = [("FULL", 0, n - 1), ("BACK", C.idx(dates, C.BACK_LO), C.idx(dates, C.BACK_HI)),
            ("TUNE", C.idx(dates, C.TUNE_LO), C.idx(dates, C.TUNE_HI))]

    def run(gate):
        bots = [dict(b, gate=gate) for b in bots0]
        return C.simulate_fleet(dates, D, bots, **kw)

    def row(name, r, gate):
        ms = [C.slice_metrics(r, lo, hi) for _, lo, hi in wins]
        blocked = (100.0 * sum(1 for g in gate if not g) / n) if gate else 0.0
        yr = dict(C.yearly(r, dates))
        return (f"| {name} | {blocked:4.0f}% | " + " | ".join(f"{m['cagr']*100:.1f}% / {m['mdd']*100:.0f}% / {m['sharpe']:.2f} / {m['mar']:.2f}" for m in ms)
                + f" | {ms[0]['trades']} | {yr.get(2018,0)*100:.0f}% / {yr.get(2020,0)*100:.0f}% / {yr.get(2022,0)*100:.0f}% |"), ms

    lines = ["| gate (entries only) | days blocked | FULL CAGR / DD / Sharpe / MAR | BACK-OOS | TUNE | trades | 2018 / 2020 / 2022 |",
             "|---|---|---|---|---|---|---|"]
    results = {}
    for name, gate in gates.items():
        r = run(gate)
        txt, ms = row(name, r, gate)
        lines.append(txt); results[name] = ms
        print(txt, flush=True)
    base = results["none (v2 as deployed)"]

    def better(ms):
        return all(ms[k]["sharpe"] > base[k]["sharpe"] and ms[k]["mar"] > base[k]["mar"] for k in (1, 2))

    survivors = [k for k, ms in results.items() if k != "none (v2 as deployed)" and better(ms)]
    print("\nSurvivors (Sharpe AND MAR better than base in BOTH BACK-OOS and TUNE):", survivors or "none")

    # threshold perturbation for the survivors (or the best FULL-Sharpe candidate if none)
    pert_lines = ["| candidate | threshold | FULL Sharpe / MAR | BACK Sharpe / MAR | TUNE Sharpe / MAR |", "|---|---|---|---|---|"]
    fam = {"VIX < 20": ("level", 20), "VIX < 25": ("level", 25), "VIX < 30": ("level", 30), "VIX < 35": ("level", 35),
           "contango VIX/VIX3M < 1.00": ("ratio", 1.0), "VIX/VIX3M < 0.95": ("ratio", 0.95), "VIX/VIX3M < 1.05": ("ratio", 1.05),
           "VIX 1y percentile < 80%": ("pct", 0.8), "VIX 1y percentile < 90%": ("pct", 0.9)}
    cands = [k for k in survivors if k in fam]
    if not cands:
        best = max((k for k in results if k != "none (v2 as deployed)"), key=lambda k: results[k][0]["sharpe"])
        cands = [best] if best in fam else []
    for k in cands:
        kind, t = fam[k]
        for f in (0.8, 1.0, 1.2):
            tt = t * f
            gate = g_level(tt) if kind == "level" else (g_ratio(tt) if kind == "ratio" else g_pct(min(tt, 0.999)))
            ms = [C.slice_metrics(run(gate), lo, hi) for _, lo, hi in wins]
            pert_lines.append(f"| {k} | {tt:.2f} | {ms[0]['sharpe']:.2f} / {ms[0]['mar']:.2f} | {ms[1]['sharpe']:.2f} / {ms[1]['mar']:.2f} | {ms[2]['sharpe']:.2f} / {ms[2]['mar']:.2f} |")
    print("\n".join(pert_lines))
    with open(os.path.join(HERE, "rq7_vix_gate.out.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n\nSurvivors: " + (", ".join(survivors) or "none") + "\n\n" + "\n".join(pert_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
