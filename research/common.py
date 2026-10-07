#!/usr/bin/env python3
"""
Shared research harness for the 2026-10 profitability review.

- fetch(): SIP daily bars from 2016-01-04 (the live/backtest code uses IEX, which
  only starts 2020-07-27 for SOXL). Cached as JSON under research/.cache/.
- simulate_fleet(): ONE engine that runs N bots on a SHARED cash pool with the
  same mechanics as backtest.py (signal at close[i], fill at open[i+1], resting
  stop / chandelier checked against each day's high/low, 10 bps per side), plus
  the live fleet rules backtest.py never modelled: shared equity for sizing,
  50% max position, cash (buying-power) constraint, 15% portfolio breaker, 10%
  per-symbol kill switch, optional core holding, optional ex-ante vol cap.
- metrics(), slice_metrics(), anchored_wf(), perturb(): the OOS tooling.

Stdlib only, Python 3.9 compatible. Nothing here places orders.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import broker              # noqa: E402
import backtest as bt      # noqa: E402
import analyze as az       # noqa: E402

CACHE = os.path.join(HERE, ".cache")
START = "2016-01-01"
COST_BPS = 10.0            # same per-side cost assumption as backtest.py

# ----------------------------------------------------------------- data

def fetch(sym, start=START, feed="sip", timeframe="1Day", refresh=False):
    """Paginated bar fetch (read-only GET) with a JSON cache."""
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, "%s_%s_%s.json" % (sym, timeframe, feed))
    if os.path.exists(fn) and not refresh:
        with open(fn) as fh:
            return json.load(fh)
    key, sec = broker.load_creds()
    bars, token = [], None
    while True:
        path = ("/v2/stocks/%s/bars?timeframe=%s&start=%s&limit=10000"
                "&adjustment=all&feed=%s" % (sym, timeframe, start, feed))
        if token:
            path += "&page_token=" + token
        j = broker.api("GET", broker.DATA_HOST, path, key, sec)
        for b in (j.get("bars") or []):
            bars.append({"t": b["t"], "date": b["t"][:10], "o": b["o"], "h": b["h"],
                         "l": b["l"], "c": b["c"], "v": b.get("v", 0)})
        token = j.get("next_page_token")
        if not token:
            break
    with open(fn, "w") as fh:
        json.dump(bars, fh)
    return bars


def load_aligned(symbols, start=START):
    """Daily bars for every symbol, aligned on common dates."""
    raw = {s: fetch(s, start) for s in symbols}
    common = None
    for s, bars in raw.items():
        ds = set(b["date"] for b in bars)
        common = ds if common is None else (common & ds)
    dates = sorted(common)
    out = {}
    for s, bars in raw.items():
        m = {b["date"]: b for b in bars}
        out[s] = [m[d] for d in dates]
    return dates, out


def idx(dates, target):
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return len(dates) - 1


# ----------------------------------------------------------------- metrics

def metrics(eq, trades=None, turnover_notional=0.0, exposure_sum=0.0):
    n = len(eq)
    cap = eq[0]
    final = eq[-1]
    years = n / 252.0
    cagr = (final / cap) ** (1 / years) - 1 if (final > 0 and years > 0) else -1.0
    peak = -1e18; mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, n) if eq[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    sharpe = mean / math.sqrt(var) * math.sqrt(252) if var > 0 else 0.0
    down = [r for r in rets if r < 0]
    dvar = sum(r * r for r in down) / len(rets) if rets else 0.0
    sortino = mean / math.sqrt(dvar) * math.sqrt(252) if dvar > 0 else 0.0
    trades = trades or []
    wins = [t for t in trades if t > 0]
    avg_eq = sum(eq) / n
    return {
        "ret": final / cap - 1, "cagr": cagr, "mdd": mdd, "sharpe": sharpe,
        "sortino": sortino, "mar": (cagr / mdd if mdd > 0 else 0.0),
        "trades": len(trades), "win": (len(wins) / len(trades)) if trades else 0.0,
        "avg_trade": (sum(trades) / len(trades)) if trades else 0.0,
        "turnover": (turnover_notional / avg_eq / years) if (avg_eq > 0 and years > 0) else 0.0,
        "exposure": exposure_sum / n if n else 0.0,
        "vol": math.sqrt(var) * math.sqrt(252),
        "days": n,
    }


def slice_metrics(res, lo, hi):
    """Metrics of a simulate_fleet() result restricted to [lo, hi] (inclusive)."""
    eq = res["eq"][lo:hi + 1]
    tr = [r for (i, r) in res["trade_log"] if lo < i <= hi]
    tn = sum(nl for (i, nl) in res["turn_log"] if lo < i <= hi)
    ex = sum(res["gross"][lo:hi + 1])
    if len(eq) < 5:
        return metrics([1.0, 1.0, 1.0, 1.0, 1.0])
    return metrics(eq, tr, tn, ex)


def fmt(m, extra=""):
    return ("ret %7.0f%%  CAGR %5.1f%%  DD %5.1f%%  Sh %5.2f  MAR %5.2f  "
            "tr %4d  win %3.0f%%  turn %4.1fx  expo %3.0f%%%s" % (
                m["ret"] * 100, m["cagr"] * 100, m["mdd"] * 100, m["sharpe"], m["mar"],
                m["trades"], m["win"] * 100, m["turnover"], m["exposure"] * 100, extra))


def md_row(label, m):
    return "| %s | %.0f%% | %.1f%% | %.1f%% | %.2f | %.2f | %d | %.1fx | %.0f%% |" % (
        label, m["ret"] * 100, m["cagr"] * 100, m["mdd"] * 100, m["sharpe"], m["mar"],
        m["trades"], m["turnover"], m["exposure"] * 100)

MD_HDR = ("| variant | return | CAGR | maxDD | Sharpe | MAR | trades | turnover | exposure |\n"
          "|---|---|---|---|---|---|---|---|---|")


# ----------------------------------------------------------------- indicators

def rsi_series(bars, period):
    return az.rsi_series(bars, period)


def realized_vol(bars, n=20):
    out = [None] * len(bars)
    rets = [0.0] + [bars[i]["c"] / bars[i - 1]["c"] - 1 for i in range(1, len(bars))]
    for i in range(n, len(bars)):
        w = rets[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / n) * math.sqrt(252)
    return out


# ----------------------------------------------------------------- bot specs

def trend_bot(sym, und, risk_pct=2.0, **kw):
    b = dict(name=sym, sym=sym, und=und, strategy="trend", risk_pct=risk_pct,
             ema_len=20, adx_min=25.0, chop=True, stop_atr=1.5, chand_atr=3.0,
             trail=True, tp_R=2.0, atr_len=14, max_pos_pct=50.0,
             mkt_confirm=True, mkt_ema=20, regime_ma=0,
             reentry="immediate",      # immediate | cooldown:N | fresh_cross
             exit_mode="ema+chand",    # ema+chand | chand_only | ema_only | ema_or_sma
             entry_fill="next_open",   # next_open | close
             lev=1.0, size_scale=None)
    b.update(kw)
    return b


def meanrev_bot(sym, und, risk_pct=2.0, **kw):
    b = dict(name=sym, sym=sym, und=und, strategy="meanrev", risk_pct=risk_pct,
             rsi_len=14, rsi_buy=30.0, rsi_sell=55.0, regime_ma=200,
             stop_atr=2.0, tp_R=2.0, trail=False, chand_atr=3.0, atr_len=14,
             max_pos_pct=50.0, mkt_confirm=False, mkt_ema=20, chop=False, adx_min=0,
             reentry="immediate", exit_mode="signal", entry_fill="next_open",
             mr_variant="rsi",         # rsi | rsi2 | pullback
             max_hold=0, lev=1.0, size_scale=None)
    b.update(kw)
    return b


def live_fleet():
    """The 7 deployed configs as of 2026-10-06."""
    return [
        trend_bot("SOXL", "SOXX", risk_pct=4.0),
        trend_bot("MSTR", "MSTR", risk_pct=2.0),
        trend_bot("PLTR", "PLTR", risk_pct=2.0),
        trend_bot("TNA", "IWM", risk_pct=2.0),
        trend_bot("UPRO", "SPY", risk_pct=2.0, mkt_confirm=False),
        meanrev_bot("TQQQ", "QQQ", risk_pct=2.0),
        meanrev_bot("LABU", "XBI", risk_pct=2.0),
    ]

FLEET_SYMBOLS = ["SOXL", "SOXX", "MSTR", "PLTR", "TNA", "IWM", "UPRO", "SPY",
                 "TQQQ", "QQQ", "LABU", "XBI"]


# ----------------------------------------------------------------- signals

def precompute(b, D, spy_gate_cache):
    """Per-bot indicator arrays + entry/exit boolean arrays (info through close[i])."""
    sig = D[b["und"]]; px = D[b["sym"]]
    n = len(sig)
    closes = [x["c"] for x in sig]
    atr = bt.atr_series(px, b["atr_len"])
    ent = [False] * n; xit = [False] * n
    if b.get("mkt_confirm"):
        k = b.get("mkt_ema", 20)
        if k not in spy_gate_cache:
            spc = [x["c"] for x in D["SPY"]]
            spe = bt.ema_series(spc, k)
            spy_gate_cache[k] = [spe[i] is not None and spc[i] > spe[i] for i in range(n)]
        mkt = spy_gate_cache[k]
    else:
        mkt = [True] * n
    if b["strategy"] == "trend":
        ema = bt.ema_series(closes, b["ema_len"])
        adx = bt.adx_series(sig, 14)
        sma = bt.sma_series(closes, b.get("regime_ma", 0)) if b.get("regime_ma") else None
        for i in range(1, n):
            if ema[i] is None or ema[i - 1] is None:
                continue
            above = closes[i] > ema[i]
            rising = ema[i] > ema[i - 1]
            ok = above and rising and mkt[i]
            if b["chop"] and (adx[i] is None or adx[i] < b["adx_min"]):
                ok = False
            if sma is not None and (sma[i] is None or closes[i] < sma[i]):
                ok = False
            ent[i] = ok
            em = b.get("exit_mode", "ema+chand")
            if em in ("ema+chand", "ema_only"):
                xit[i] = not above
            elif em == "chand_only":
                xit[i] = False
            elif em == "ema_or_sma":      # exit only on a deeper break: SMA50
                s50 = bt.sma_series(closes, 50)
                xit[i] = s50[i] is not None and closes[i] < s50[i]
        if b.get("gate") is not None:
            ent = [ent[i] and b["gate"][i] for i in range(n)]
        return dict(atr=atr, ent=ent, xit=xit, ema=ema, adx=adx)
    # meanrev
    rsi = rsi_series(sig, b["rsi_len"])
    sma = bt.sma_series(closes, b.get("regime_ma", 0)) if b.get("regime_ma") else None
    v = b.get("mr_variant", "rsi")
    ema20 = bt.ema_series(closes, 20)
    sma5 = bt.sma_series(closes, 5)
    for i in range(1, n):
        reg = True if sma is None else (sma[i] is not None and closes[i] > sma[i])
        if v == "rsi":
            e = rsi[i] is not None and rsi[i] < b["rsi_buy"]
            x = rsi[i] is not None and rsi[i] > b["rsi_sell"]
        elif v == "rsi2":
            e = rsi[i] is not None and rsi[i] < b["rsi_buy"]
            x = (rsi[i] is not None and rsi[i] > b["rsi_sell"]) or \
                (sma5[i] is not None and closes[i] > sma5[i])
        elif v == "pullback":    # close under EMA20 (dip) inside the regime; exit back above
            e = ema20[i] is not None and closes[i] < ema20[i] * (1 - b.get("pb_pct", 0.0))
            x = ema20[i] is not None and closes[i] > ema20[i]
        else:
            raise ValueError(v)
        ent[i] = bool(e and reg and mkt[i])
        xit[i] = bool(x)
    if b.get("gate") is not None:
        ent = [ent[i] and b["gate"][i] for i in range(n)]
    return dict(atr=atr, ent=ent, xit=xit, rsi=rsi)


# ----------------------------------------------------------------- engine

def simulate_fleet(dates, D, bots, capital=100000.0, max_gross=1.0,
                   port_break=0.15, sym_kill=0.10, core=None, vol_cap=None,
                   cost_bps=COST_BPS, margin_rate=0.07, size_on="equity",
                   port_scale=None):
    """Run N bots on one shared cash pool. Returns dict(eq, gross, trade_log,
    turn_log, per_bot, breaker_days, kill_days, core_value)."""
    n = len(dates)
    cost = cost_bps / 10000.0
    spy_cache = {}
    B = []
    for b in bots:
        pc = precompute(b, D, spy_cache)
        B.append(dict(cfg=b, pc=pc, px=D[b["sym"]], pos=None, pend_exit=False,
                      pend_entry=None, last_exit_i=-10 ** 6, trades=[], turn=0.0,
                      last_exit_was_stop=False, below_since_exit=False, hold_days=0))
    vols = {}
    if vol_cap:
        for bb in B:
            vols[bb["cfg"]["sym"]] = realized_vol(bb["px"], 20)
    # core sleeve
    core_sh = 0.0; core_px = None; core_pending = None; core_sma = None; core_on = False
    if core:
        core_px = D[core["sym"]]
        if core.get("sma"):
            core_sma = bt.sma_series([x["c"] for x in core_px], core["sma"])
    cash = capital
    eq = []; gross = []; trade_log = []; turn_log = []
    breaker_days = []; kill_days = []
    day_start_eq = capital
    halted_today = False
    for i in range(n):
        halted_today = False
        # --- core sleeve fills at open
        if core:
            o = core_px[i]["o"]
            if i == 0 and not core.get("sma"):
                amt = capital * core["frac"]
                core_sh = amt / (o * (1 + cost)); cash -= amt; core_on = True
                turn_log.append((i, amt))
            if core_pending == "buy" and core_sh == 0:
                # size the core as frac of CURRENT equity
                eq_now = cash + sum(bb["pos"]["sh"] * bb["px"][i]["o"] for bb in B if bb["pos"])
                amt = min(cash, eq_now * core["frac"])
                if amt > 0:
                    core_sh = amt / (o * (1 + cost)); cash -= amt; turn_log.append((i, amt))
                core_on = True
            elif core_pending == "sell" and core_sh > 0:
                cash += core_sh * o * (1 - cost); turn_log.append((i, core_sh * o)); core_sh = 0.0
                core_on = False
            core_pending = None
        # --- bot fills at open
        for bb in B:
            bar = bb["px"][i]; o = bar["o"]
            cfgb = bb["cfg"]
            if bb["pend_exit"] and bb["pos"]:
                p = bb["pos"]
                cash += p["sh"] * o * (1 - cost); turn_log.append((i, p["sh"] * o))
                r = o / p["entry"] - 1; bb["trades"].append(r); trade_log.append((i, r))
                bb["pos"] = None; bb["last_exit_i"] = i; bb["last_exit_was_stop"] = False
            bb["pend_exit"] = False
            if bb["pend_entry"] and bb["pos"] is None:
                sh, stop, tp = bb["pend_entry"]
                # cash / gross constraint at fill time (buying power)
                eq_now = cash + sum(x["pos"]["sh"] * x["px"][i]["o"] for x in B if x["pos"]) \
                    + core_sh * (core_px[i]["o"] if core else 0)
                lev = cfgb.get("lev", 1.0)
                max_cash = cash + max(0.0, (max_gross - 1.0) * eq_now)   # margin room
                if sh * o * (1 + cost) > max_cash:
                    sh = int(max_cash / (o * (1 + cost)))
                if sh >= 1:
                    cash -= sh * o * (1 + cost); turn_log.append((i, sh * o))
                    bb["pos"] = {"sh": sh, "entry": o, "stop": stop, "tp": tp,
                                 "hh": bar["h"], "atr": bb["pc"]["atr"][i] or 0.0, "entry_i": i}
            bb["pend_entry"] = None
        # --- intraday resting orders
        for bb in B:
            p = bb["pos"]
            if not p:
                continue
            cfgb = bb["cfg"]; bar = bb["px"][i]
            o, h, l = bar["o"], bar["h"], bar["l"]
            stop = p["stop"]
            if cfgb["trail"] and p["atr"] and cfgb.get("exit_mode") != "ema_only":
                stop = max(stop, p["hh"] - cfgb["chand_atr"] * p["atr"])
            if l <= stop:
                fill = min(o, stop)
                cash += p["sh"] * fill * (1 - cost); turn_log.append((i, p["sh"] * fill))
                r = fill / p["entry"] - 1; bb["trades"].append(r); trade_log.append((i, r))
                bb["pos"] = None; bb["last_exit_i"] = i; bb["last_exit_was_stop"] = True
            elif (not cfgb["trail"]) and h >= p["tp"]:
                fill = max(o, p["tp"])
                cash += p["sh"] * fill * (1 - cost); turn_log.append((i, p["sh"] * fill))
                r = fill / p["entry"] - 1; bb["trades"].append(r); trade_log.append((i, r))
                bb["pos"] = None; bb["last_exit_i"] = i; bb["last_exit_was_stop"] = False
            else:
                p["hh"] = max(p["hh"], h)
        # --- margin interest on negative cash
        if cash < 0:
            cash += cash * margin_rate / 252.0
        # --- mark to close
        pos_val = sum(bb["pos"]["sh"] * bb["px"][i]["c"] for bb in B if bb["pos"])
        core_val = core_sh * core_px[i]["c"] if core else 0.0
        eq_close = cash + pos_val + core_val
        # --- breakers (evaluated at close; live checks every 15 min)
        if port_break and day_start_eq > 0 and (eq_close - day_start_eq) / day_start_eq <= -port_break:
            for bb in B:
                p = bb["pos"]
                if p:
                    c = bb["px"][i]["c"]
                    cash += p["sh"] * c * (1 - cost); turn_log.append((i, p["sh"] * c))
                    r = c / p["entry"] - 1; bb["trades"].append(r); trade_log.append((i, r))
                    bb["pos"] = None; bb["last_exit_i"] = i
            breaker_days.append(dates[i]); halted_today = True
            pos_val = 0.0; eq_close = cash + core_val
        elif sym_kill and day_start_eq > 0:
            for bb in B:
                p = bb["pos"]
                if not p:
                    continue
                ref = bb["px"][i - 1]["c"] if p.get("entry_i", i) < i else p["entry"]
                pnl = p["sh"] * (bb["px"][i]["c"] - ref)
                if pnl <= -sym_kill * day_start_eq:
                    c = bb["px"][i]["c"]
                    cash += p["sh"] * c * (1 - cost); turn_log.append((i, p["sh"] * c))
                    r = c / p["entry"] - 1; bb["trades"].append(r); trade_log.append((i, r))
                    bb["pos"] = None; bb["last_exit_i"] = i
                    kill_days.append((dates[i], bb["cfg"]["name"]))
            pos_val = sum(bb["pos"]["sh"] * bb["px"][i]["c"] for bb in B if bb["pos"])
            eq_close = cash + pos_val + core_val
        # --- core timing signal
        if core and core.get("sma"):
            s = core_sma[i]; c = core_px[i]["c"]
            if s is not None:
                if c > s and core_sh == 0:
                    core_pending = "buy"
                elif c < s and core_sh > 0:
                    core_pending = "sell"
        # --- end-of-day signals
        reserved = 0.0
        used_vol = 0.0
        if vol_cap:
            for bb in B:
                if bb["pos"]:
                    v = vols[bb["cfg"]["sym"]][i] or 0.0
                    used_vol += bb["pos"]["sh"] * bb["px"][i]["c"] / eq_close * v
        for bb in B:
            cfgb = bb["cfg"]; pc = bb["pc"]
            if halted_today:
                break
            p = bb["pos"]
            if p:
                bb["hold_days"] += 1
                mh = cfgb.get("max_hold", 0)
                if pc["xit"][i] or (mh and bb["hold_days"] >= mh):
                    bb["pend_exit"] = True
                continue
            bb["hold_days"] = 0
            if not pc["ent"][i] or not pc["atr"][i]:
                # track "fresh cross" requirement
                if cfgb.get("reentry") == "fresh_cross" and cfgb["strategy"] == "trend":
                    ema = pc["ema"][i]
                    if ema is not None and D[cfgb["und"]][i]["c"] < ema:
                        bb["below_since_exit"] = True
                continue
            re = cfgb.get("reentry", "immediate")
            if re.startswith("cooldown"):
                k = int(re.split(":")[1])
                if bb["last_exit_was_stop"] and i - bb["last_exit_i"] < k:
                    continue
            elif re == "fresh_cross":
                if bb["last_exit_was_stop"] and not bb["below_since_exit"]:
                    continue
            bb["below_since_exit"] = False
            # sizing (live: risk_pct of TOTAL account equity, stop = stop_atr x own ATR)
            base_eq = eq_close if size_on == "equity" else max(0.0, eq_close - core_val)
            rp = cfgb["risk_pct"]
            if cfgb.get("size_scale") is not None:
                rp *= cfgb["size_scale"][i]
            if port_scale is not None:
                rp *= port_scale[i]
            a = pc["atr"][i]
            stop_dist = cfgb["stop_atr"] * a
            if stop_dist <= 0:
                continue
            est = bb["px"][i]["c"]
            lev = cfgb.get("lev", 1.0)
            sh = int(base_eq * rp / 100.0 / stop_dist * lev)
            max_notional = base_eq * cfgb["max_pos_pct"] / 100.0 * lev
            if vol_cap:
                v = vols[cfgb["sym"]][i] or 0.0
                room = max(0.0, vol_cap - used_vol)
                if v > 0:
                    max_notional = min(max_notional, room / v * eq_close)
            room_cash = cash - reserved + max(0.0, (max_gross - 1.0) * eq_close)
            notional = min(sh * est, max_notional, room_cash)
            sh = int(notional / est) if est > 0 else 0
            if sh < 1:
                continue
            reserved += sh * est
            if vol_cap:
                used_vol += sh * est / eq_close * (vols[cfgb["sym"]][i] or 0.0)
            stop = est - stop_dist
            tp = est + stop_dist * (8.0 if cfgb["trail"] else cfgb["tp_R"])
            if cfgb.get("entry_fill") == "close":
                # fill at today's close (approximates the 15:45 tick)
                cash -= sh * est * (1 + cost); turn_log.append((i, sh * est))
                bb["pos"] = {"sh": sh, "entry": est, "stop": stop, "tp": tp,
                             "hh": bb["px"][i]["h"], "atr": a, "entry_i": i}
            else:
                bb["pend_entry"] = (sh, stop, tp)
        pos_val = sum(bb["pos"]["sh"] * bb["px"][i]["c"] for bb in B if bb["pos"])
        eq_close = cash + pos_val + core_val
        eq.append(eq_close)
        gross.append((pos_val + core_val) / eq_close if eq_close > 0 else 0.0)
        day_start_eq = eq_close
    per_bot = {bb["cfg"]["name"]: bb["trades"] for bb in B}
    return dict(eq=eq, gross=gross, trade_log=trade_log, turn_log=turn_log,
                per_bot=per_bot, breaker_days=breaker_days, kill_days=kill_days)


def buy_hold(dates, bars, capital=100000.0, cost_bps=COST_BPS):
    c0 = bars[0]["c"] * (1 + cost_bps / 10000.0)
    eq = [capital * b["c"] / c0 for b in bars]
    return dict(eq=eq, gross=[1.0] * len(eq), trade_log=[(1, eq[-1] / capital - 1)],
                turn_log=[(1, capital)], per_bot={}, breaker_days=[], kill_days=[])


def blend(curves_weights):
    """Daily-rebalanced blend of equity curves (fractions must sum to <= 1; rest cash)."""
    n = len(curves_weights[0][0])
    eq = [curves_weights[0][0][0]]
    for i in range(1, n):
        r = 0.0
        for c, w in curves_weights:
            r += w * (c[i] / c[i - 1] - 1)
        eq.append(eq[-1] * (1 + r))
    return eq


# ----------------------------------------------------------------- walk-forward

def year_bounds(dates, year):
    lo = idx(dates, "%d-01-01" % year)
    hi = idx(dates, "%d-01-01" % (year + 1)) - 1
    if dates[-1] < "%d-01-01" % (year + 1):
        hi = len(dates) - 1
    return lo, hi


def anchored_wf(dates, results, first_oos_year, last_oos_year, key="mar", min_is_days=500):
    """results: {label: sim_result}. For each OOS year select the label with the
    best IS `key` on all data BEFORE that year, then take that label's OOS-year
    returns. Returns (stitched OOS metrics, chosen labels per year, stitched eq)."""
    chosen = []
    eq = [100000.0]
    for y in range(first_oos_year, last_oos_year + 1):
        lo, hi = year_bounds(dates, y)
        if lo - 1 < min_is_days:
            continue
        best = None; best_v = -1e18
        for lab, res in results.items():
            m = slice_metrics(res, 0, lo - 1)
            v = m[key]
            if v > best_v:
                best, best_v = lab, v
        chosen.append((y, best))
        e = results[best]["eq"]
        for i in range(lo, hi + 1):
            if i == 0:
                continue
            eq.append(eq[-1] * (e[i] / e[i - 1]))
    m = metrics(eq)
    return m, chosen, eq


def perturb(base_cfg, keys, pct=0.20):
    """Yield (label, cfg) for +-pct perturbations of each numeric key, plus all-up / all-down."""
    out = [("base", dict(base_cfg))]
    for k in keys:
        for sgn in (-1, 1):
            c = dict(base_cfg)
            v = base_cfg[k]
            nv = v * (1 + sgn * pct)
            if isinstance(v, int):
                nv = max(1, int(round(nv)))
            c[k] = nv
            out.append(("%s%s" % (k, "+" if sgn > 0 else "-"), c))
    for sgn, lab in ((-1, "all-"), (1, "all+")):
        c = dict(base_cfg)
        for k in keys:
            v = base_cfg[k]; nv = v * (1 + sgn * pct)
            if isinstance(v, int):
                nv = max(1, int(round(nv)))
            c[k] = nv
        out.append((lab, c))
    return out


# ----------------------------------------------------------------- fleet data

def load_fleet_data(start=START):
    """All fleet symbols aligned from 2016. PLTR (IPO 2020-09-30) is padded with
    flat pre-IPO bars so its bot simply never signals before listing."""
    syms = [s for s in FLEET_SYMBOLS if s != "PLTR"]
    dates, D = load_aligned(syms, start)
    p = fetch("PLTR", start)
    pm = {b["date"]: b for b in p}
    first = p[0]
    pad = {"date": None, "o": first["o"], "h": first["o"], "l": first["o"], "c": first["o"], "v": 0}
    D["PLTR"] = [pm.get(d, dict(pad, date=d)) for d in dates]
    return dates, D


def full(res):
    return slice_metrics(res, 0, len(res["eq"]) - 1)


def window(res, dates, lo_date, hi_date):
    lo = idx(dates, lo_date); hi = idx(dates, hi_date)
    if dates[hi] > hi_date:
        hi -= 1
    return slice_metrics(res, lo, hi)


# the window the deployed parameters were tuned on (IEX data started 2020-08-28
# in backtest.py's 6y window; configs were frozen 2026-06-12)
TUNE_LO, TUNE_HI = "2020-08-28", "2026-06-12"
BACK_LO, BACK_HI = "2016-01-04", "2020-08-27"     # never seen by any prior research
FWD_LO, FWD_HI = "2026-06-13", "2026-12-31"       # live period (tiny)

WINDOWS = [("FULL 2016-2026", "2016-01-04", "2026-12-31"),
           ("BACKWARD OOS 2016-2020.08", BACK_LO, BACK_HI),
           ("TUNING WINDOW 2020.08-2026.06", TUNE_LO, TUNE_HI),
           ("2022 bear", "2022-01-01", "2022-12-31"),
           ("LIVE 2026.06-10", FWD_LO, FWD_HI)]


def yearly(res, dates):
    out = []
    y0 = int(dates[0][:4]); y1 = int(dates[-1][:4])
    for y in range(y0, y1 + 1):
        lo, hi = year_bounds(dates, y)
        if hi <= lo:
            continue
        e = res["eq"]
        base = e[lo - 1] if lo > 0 else e[0]
        out.append((y, e[hi] / base - 1))
    return out


def drawdowns(eq, dates, top=3):
    """Top peak-to-trough episodes: (depth, peak_date, trough_date, recovery_date)."""
    eps = []
    peak = eq[0]; pi = 0; ti = 0; trough = eq[0]
    for i, v in enumerate(eq):
        if v >= peak:
            if trough < peak:
                eps.append(((peak - trough) / peak, dates[pi], dates[ti], dates[i]))
            peak = v; pi = i; trough = v; ti = i
        elif v < trough:
            trough = v; ti = i
    if trough < peak:
        eps.append(((peak - trough) / peak, dates[pi], dates[ti], "open"))
    eps.sort(reverse=True)
    return eps[:top]
