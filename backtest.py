#!/usr/bin/env python3
"""
Backtest harness for the SOXL strategy.

Realism choices:
- Signals come from SOXX (the index); P&L is realized on ACTUAL SOXL daily bars,
  so the 3x daily-reset volatility decay is fully captured (not approximated by 3x).
- Signal at close[i]; entry/trend-exit fills at open[i+1] (no same-bar lookahead).
- Stop / take-profit / trailing stop are resting orders checked intraday against
  each day's high/low (gaps fill at the open).

Features (toggled per config, so we ADOPT ONLY WHAT BEATS THE BASELINE):
- chop filter  : require ADX(SOXX) > adx_min to enter (sit out non-trending tape)
- trailing exit: chandelier stop = highest-high-since-entry - chand_atr*ATR
- vol scaling  : shrink risk when SOXL ATR% is above vol_ref (constant dollar vol)
- event gating : block new entries within event_block days of a known event date

Usage:
  python3 backtest.py                 # compare baseline vs each improvement
  python3 backtest.py --years 6
"""

from __future__ import annotations

import argparse
import math

import broker

# ----- data ---------------------------------------------------------------

def fetch(symbol, start, key, sec):
    bars, token = [], None
    while True:
        path = (f"/v2/stocks/{symbol}/bars?timeframe=1Day&start={start}"
                f"&limit=10000&adjustment=all&feed=iex")
        if token:
            path += f"&page_token={token}"
        j = broker.api("GET", broker.DATA_HOST, path, key, sec)
        for b in (j.get("bars") or []):
            bars.append({"date": b["t"][:10], "o": b["o"], "h": b["h"],
                         "l": b["l"], "c": b["c"]})
        token = j.get("next_page_token")
        if not token:
            break
    return bars


def align(soxx, soxl):
    a = {b["date"]: b for b in soxx}
    b = {x["date"]: x for x in soxl}
    dates = sorted(set(a) & set(b))
    return dates, [a[d] for d in dates], [b[d] for d in dates]


# ----- indicators ---------------------------------------------------------

def ema_series(vals, period):
    k = 2 / (period + 1)
    out = [None] * len(vals)
    if len(vals) < period:
        return out
    e = sum(vals[:period]) / period
    out[period - 1] = e
    for i in range(period, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr_series(bars, period):
    """Wilder ATR."""
    out = [None] * len(bars)
    trs = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr = bar["h"] - bar["l"]
        else:
            pc = bars[i - 1]["c"]
            tr = max(bar["h"] - bar["l"], abs(bar["h"] - pc), abs(bar["l"] - pc))
        trs.append(tr)
    if len(trs) <= period:
        return out
    a = sum(trs[1:period + 1]) / period
    out[period] = a
    for i in range(period + 1, len(bars)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def adx_series(bars, period=14):
    """Wilder ADX on the index. Returns list aligned to bars."""
    n = len(bars)
    out = [None] * n
    tr = [0.0] * n; pdm = [0.0] * n; ndm = [0.0] * n
    for i in range(1, n):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        pc = bars[i - 1]["c"]
        tr[i] = max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc),
                    abs(bars[i]["l"] - pc))
    if n <= 2 * period:
        return out
    # Wilder smoothing
    str_ = sum(tr[1:period + 1]); spdm = sum(pdm[1:period + 1]); sndm = sum(ndm[1:period + 1])
    dxs = []
    for i in range(period + 1, n):
        str_ = str_ - str_ / period + tr[i]
        spdm = spdm - spdm / period + pdm[i]
        sndm = sndm - sndm / period + ndm[i]
        pdi = 100 * spdm / str_ if str_ else 0
        ndi = 100 * sndm / str_ if str_ else 0
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0
        dxs.append((i, dx))
    if len(dxs) < period:
        return out
    adx = sum(d for _, d in dxs[:period]) / period
    out[dxs[period - 1][0]] = adx
    for j in range(period, len(dxs)):
        adx = (adx * (period - 1) + dxs[j][1]) / period
        out[dxs[j][0]] = adx
    return out


# ----- simulation ---------------------------------------------------------

DEFAULTS = dict(
    capital=100_000.0, risk_pct=2.0, ema_len=20, atr_len=14,
    stop_atr=1.5, tp_R=2.0,
    chop=False, adx_min=25.0,
    trail=False, chand_atr=3.0,
    volscale=False, vol_ref=0.11,         # ~11% daily ATR is "normal" for SOXL
    event_block=0, event_dates=frozenset(),
    shorts=False,                          # long/short: hold SOXS in downtrends
    regime_ma=0,                           # only go long if SOXX > SMA(regime_ma)
)


def sma_series(vals, period):
    out = [None] * len(vals)
    if period <= 0:
        return out
    run = 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= period:
            run -= vals[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def align3(soxx, soxl, soxs):
    a = {b["date"]: b for b in soxx}
    b_ = {x["date"]: x for x in soxl}
    c = {x["date"]: x for x in soxs}
    dates = sorted(set(a) & set(b_) & set(c))
    return dates, [a[d] for d in dates], [b_[d] for d in dates], [c[d] for d in dates]


def simulate_ls(dates, soxx, soxl, soxs, p):
    """Long SOXL in confirmed uptrends, long SOXS in confirmed downtrends, flat in
    chop. Same trailing/stop/sizing machinery, applied to whichever ETF is held."""
    ema = ema_series([b["c"] for b in soxx], p["ema_len"])
    adx = adx_series(soxx, 14)
    bars = {"SOXL": soxl, "SOXS": soxs}
    atrs = {"SOXL": atr_series(soxl, p["atr_len"]),
            "SOXS": atr_series(soxs, p["atr_len"])}
    cash = p["capital"]; pos = None; pending = None
    eq_curve = []; trades = []; days_in = 0

    for i in range(len(dates)):
        if pending:
            if pending[0] == "exit" and pos:
                o = bars[pos["sym"]][i]["o"]
                cash += pos["sh"] * o; trades.append(o / pos["entry"] - 1); pos = None
            elif pending[0] == "entry" and pos is None:
                _, sym, sh, stop, tp = pending
                o = bars[sym][i]["o"]; cash -= sh * o
                pos = {"sym": sym, "sh": sh, "entry": o, "stop": stop, "tp": tp,
                       "hh": bars[sym][i]["h"], "atr": atrs[sym][i] or 0}
            pending = None

        if pos:
            days_in += 1
            bar = bars[pos["sym"]][i]
            o, h, l = bar["o"], bar["h"], bar["l"]
            stop = pos["stop"]
            if p["trail"] and pos["atr"]:
                stop = max(stop, pos["hh"] - p["chand_atr"] * pos["atr"])
            if l <= stop:
                fill = min(o, stop); cash += pos["sh"] * fill
                trades.append(fill / pos["entry"] - 1); pos = None
            elif (not p["trail"]) and h >= pos["tp"]:
                fill = max(o, pos["tp"]); cash += pos["sh"] * fill
                trades.append(fill / pos["entry"] - 1); pos = None
            else:
                pos["hh"] = max(pos["hh"], h)

        if ema[i] is not None and ema[i - 1] is not None:
            rising = ema[i] > ema[i - 1]; cx = soxx[i]["c"]
            adx_ok = (not p["chop"]) or (adx[i] is not None and adx[i] >= p["adx_min"])
            up = cx > ema[i] and rising and adx_ok
            down = cx < ema[i] and (not rising) and adx_ok
            if pos:
                if pos["sym"] == "SOXL" and not (cx > ema[i]):
                    pending = ("exit",)
                elif pos["sym"] == "SOXS" and not (cx < ema[i]):
                    pending = ("exit",)
            else:
                sym = "SOXL" if up else ("SOXS" if (down and p["shorts"]) else None)
                if sym and atrs[sym][i]:
                    sd = p["stop_atr"] * atrs[sym][i]
                    est = bars[sym][i]["c"]
                    sh = int((cash * p["risk_pct"] / 100) / sd)
                    if sh >= 1 and sh * est <= cash:
                        pending = ("entry", sym, sh, est - sd, est + sd * p["tp_R"])

        eq_curve.append(cash + (pos["sh"] * bars[pos["sym"]][i]["c"] if pos else 0))

    return metrics(dates, eq_curve, trades, days_in, p["capital"])


def simulate(dates, soxx, soxl, p):
    ema = ema_series([b["c"] for b in soxx], p["ema_len"])
    adx = adx_series(soxx, 14)
    atr = atr_series(soxl, p["atr_len"])
    sma = sma_series([b["c"] for b in soxx], p.get("regime_ma", 0))

    cash = p["capital"]; pos = None
    pending_entry = pending_exit = False
    eq_curve = []; trades = []; days_in = 0
    ev = p["event_dates"]; evb = p["event_block"]
    cost = p.get("cost_bps", 0) / 10000.0   # per-side transaction cost
    eg = p.get("entry_gate")                 # optional per-day bool: allow entry
    ss = p.get("size_scale")                 # optional per-day risk multiplier

    def near_event(idx):
        if not ev or evb <= 0:
            return False
        for d in range(idx, min(idx + evb + 1, len(dates))):
            if dates[d] in ev:
                return True
        return False

    for i in range(len(dates)):
        o, h, l, c = soxl[i]["o"], soxl[i]["h"], soxl[i]["l"], soxl[i]["c"]

        # 1. execute pending fills at today's open
        if pending_exit and pos:
            cash += pos["sh"] * o * (1 - cost)
            trades.append(o / pos["entry"] - 1)
            pos = None; pending_exit = False
        if pending_entry and pos is None:
            sh = pending_entry
            cash -= sh * o * (1 + cost)
            pos = {"sh": sh, "entry": o, "stop": pend_stop, "tp": pend_tp,
                   "hh": h, "atr": atr[i] or 0}
            pending_entry = False
        elif pending_entry:
            pending_entry = False

        # 2. resting stop / TP / trailing checked intraday
        if pos:
            days_in += 1
            stop = pos["stop"]
            if p["trail"] and pos["atr"]:
                stop = max(stop, pos["hh"] - p["chand_atr"] * pos["atr"])
            if l <= stop:                      # stop hit (gap -> open)
                fill = min(o, stop)
                cash += pos["sh"] * fill * (1 - cost); trades.append(fill / pos["entry"] - 1); pos = None
            elif (not p["trail"]) and h >= pos["tp"]:
                fill = max(o, pos["tp"])
                cash += pos["sh"] * fill * (1 - cost); trades.append(fill / pos["entry"] - 1); pos = None
            else:
                pos["hh"] = max(pos["hh"], h)

        # 3. end-of-day signals (info through close[i])
        if ema[i] is not None and ema[i - 1] is not None:
            rising = ema[i] > ema[i - 1]
            long_ok = c_soxx(soxx, i) > ema[i] and rising
            if p["chop"] and (adx[i] is None or adx[i] < p["adx_min"]):
                long_ok = False
            if p.get("regime_ma", 0) and (sma[i] is None or c_soxx(soxx, i) < sma[i]):
                long_ok = False
            if pos and not (c_soxx(soxx, i) > ema[i]):
                pending_exit = True
            elif (pos is None and long_ok and not near_event(i) and atr[i]
                  and (eg is None or eg[i])):
                # size at next open ~ today's close; risk = risk_pct of equity
                eq = cash
                rp = p["risk_pct"]
                if ss is not None:
                    rp = rp * ss[i]
                if p["volscale"]:
                    atr_pct = atr[i] / soxl[i]["c"]
                    rp = rp * min(1.0, p["vol_ref"] / atr_pct) if atr_pct else rp
                stop_dist = p["stop_atr"] * atr[i]
                if stop_dist > 0:
                    sh = int((eq * rp / 100) / stop_dist)
                    entry_est = soxl[i]["c"]
                    if sh >= 1 and sh * entry_est <= eq:   # no leverage beyond cash
                        pending_entry = sh
                        pend_stop = entry_est - stop_dist
                        pend_tp = entry_est + (entry_est - pend_stop) * p["tp_R"]

        eq_curve.append(cash + (pos["sh"] * c if pos else 0))

    res = metrics(dates, eq_curve, trades, days_in, p["capital"])
    res["eq"] = eq_curve
    return res


def c_soxx(soxx, i):
    return soxx[i]["c"]


def metrics(dates, eq, trades, days_in, cap):
    final = eq[-1]
    years = len(eq) / 252
    cagr = (final / cap) ** (1 / years) - 1 if final > 0 and years > 0 else -1
    peak = -1e18; mdd = 0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0
    sharpe = (mean / math.sqrt(var) * math.sqrt(252)) if var > 0 else 0
    wins = [t for t in trades if t > 0]
    return {
        "final": final, "ret": final / cap - 1, "cagr": cagr, "mdd": mdd,
        "sharpe": sharpe, "trades": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else 0,
        "avg_trade": (sum(trades) / len(trades)) if trades else 0,
        "exposure": days_in / len(dates),
    }


def cfg(**kw):
    p = dict(DEFAULTS); p.update(kw); return p


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=6)
    args = ap.parse_args(argv)
    import datetime as dt
    start = (dt.date.today() - dt.timedelta(days=int(args.years * 365 + 40))).isoformat()

    key, sec = broker.load_creds()
    soxx = fetch("SOXX", start, key, sec)
    soxl = fetch("SOXL", start, key, sec)
    soxs = fetch("SOXS", start, key, sec)
    spy = fetch("SPY", start, key, sec)
    dates, soxx, soxl, soxs = align3(soxx, soxl, soxs)
    print(f"Backtest window: {dates[0]} -> {dates[-1]}  ({len(dates)} days)\n")
    bh = soxl[-1]["c"] / soxl[0]["c"] - 1

    # SPY broad-market confirmation gate (live mkt_confirm), aligned to our dates.
    spyd = {b["date"]: b for b in spy}
    spc = [spyd[d]["c"] if d in spyd else None for d in dates]
    spe = ema_series([c if c is not None else 0 for c in spc], 20)
    spy_gate = [(spc[i] is not None and spe[i] is not None and spc[i] > spe[i])
                or spc[i] is None for i in range(len(dates))]

    def row(name, m):
        print(f"{name:30} {m['ret']*100:>8.0f}% {m['cagr']*100:>6.0f}% "
              f"{m['mdd']*100:>6.0f}% {m['sharpe']:>7.2f} {m['trades']:>7} "
              f"{m['win_rate']*100:>5.0f}% {m['exposure']*100:>5.0f}%")

    hdr = f"{'strategy':30} {'return':>9} {'CAGR':>7} {'maxDD':>7} {'Sharpe':>7} {'trades':>7} {'win%':>6} {'expo':>6}"

    print("== STRATEGY VARIANTS (risk 4%, ~10bps cost) ==")
    print(hdr); print("-" * len(hdr))
    live = cfg(chop=True, trail=True, risk_pct=4.0, cost_bps=10, entry_gate=spy_gate)
    row("CURRENT LIVE (chop+trail+SPY)", simulate(dates, soxx, soxl, live))
    row("  without SPY confirm", simulate(dates, soxx, soxl,
                                          cfg(chop=True, trail=True, risk_pct=4.0, cost_bps=10)))
    row("  long/SHORT (rejected)", simulate_ls(dates, soxx, soxl, soxs,
                                               cfg(chop=True, trail=True, risk_pct=4.0, shorts=True)))
    print("-" * len(hdr))
    print(f"{'Buy & hold SOXL':30} {bh*100:>8.0f}%   (~90% drawdown)\n")

    print("== RISK-LEVEL SWEEP (the leverage/return/drawdown trade-off) ==")
    print(hdr); print("-" * len(hdr))
    for rp in (2, 3, 4, 6):
        row(f"Long-only  risk {rp}%", simulate(dates, soxx, soxl,
                                               cfg(chop=True, trail=True, risk_pct=rp)))
    for rp in (2, 3, 4):
        row(f"Long/short risk {rp}%", simulate_ls(dates, soxx, soxl, soxs,
                                                  cfg(chop=True, trail=True, shorts=True, risk_pct=rp)))
    print("-" * len(hdr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
