#!/usr/bin/env python3
"""RQ3 (intraday): the live bot ticks every 15 min and evaluates EMA/ADX/exit on
the PARTIAL current-day bar (Alpaca's 1Day bars include today's in-progress bar),
so it can enter at 10:15 and exit at 11:00 on the same day. backtest.py assumes
signals on completed closes with fills at the next open. This replays 30-minute
SIP bars 2016-2026 through the live functions (broker.ema_pair / adx / atr on a
~110-bar window, exactly as bot.py does) in three modes:

  live      : evaluate at every 30-min tick on the partial bar, fill at that tick
  eod       : evaluate once at the last tick (15:30 bar close ~ the 15:45 tick), fill at close
  nextopen  : completed-bar signal, fill at next day's 09:30 open (backtest convention)

Usage: python3 research/rq3_intraday.py [SOXL ...]
"""
import sys, os, datetime as dt
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C
import broker

ET = ZoneInfo("America/New_York")
LOOKBACK = 110          # bars the live 160-calendar-day fetch returns
COST = C.COST_BPS / 10000.0
PAIRS = {"SOXL": ("SOXX", 4.0, True), "UPRO": ("SPY", 2.0, False), "TNA": ("IWM", 2.0, True),
         "MSTR": ("MSTR", 2.0, True)}


def session(sym):
    out = {}
    for b in C.fetch(sym, timeframe="30Min"):
        t = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        hm = t.hour * 100 + t.minute
        if hm < 930 or hm >= 1600:
            continue
        out.setdefault(t.date().isoformat(), []).append(dict(b, hm=hm))
    return out


def run(sym, und, rp, mk, mode, use_spy=True):
    dsym = {b["date"]: b for b in C.fetch(sym)}
    dund = {b["date"]: b for b in C.fetch(und)}
    dspy = {b["date"]: b for b in C.fetch("SPY")}
    ssym = session(sym); sund = session(und); sspy = session("SPY") if (mk and use_spy) else {}
    dates = sorted(set(dsym) & set(dund) & set(ssym) & set(sund) & set(dspy) & (set(sspy) if sspy else set(dsym)))
    und_d = [dund[d] for d in dates]; sym_d = [dsym[d] for d in dates]; spy_d = [dspy[d] for d in dates]
    cash = 100000.0; pos = None; eq = []; trades = []; same_day = 0; turn = 0.0; gross = []
    pend_entry = None
    for i in range(LOOKBACK + 1, len(dates)):
        d = dates[i]
        hu = und_d[i - LOOKBACK:i]; hs = sym_d[i - LOOKBACK:i]; hp = spy_d[i - LOOKBACK:i]
        tu = {b["hm"]: b for b in sund[d]}; ts = {b["hm"]: b for b in ssym[d]}
        tp = {b["hm"]: b for b in sspy.get(d, [])}
        hms = sorted(set(tu) & set(ts) & (set(tp) if sspy else set(tu)))
        if not hms:
            eq.append(cash + (pos["sh"] * sym_d[i]["c"] if pos else 0)); gross.append(0); continue
        pu = ps = pp = None
        entered_today = False
        for k, hm in enumerate(hms):
            bu, bs = tu[hm], ts[hm]
            bp = tp.get(hm) if sspy else None
            # pending next-open entry fills at the first bar's open
            if mode == "nextopen" and k == 0 and pend_entry and pos is None:
                sh, stop, tp_ = pend_entry; o = bs["o"]
                sh = min(sh, int(cash / (o * (1 + COST))))
                if sh >= 1:
                    cash -= sh * o * (1 + COST); turn += sh * o
                    pos = dict(sh=sh, entry=o, stop=stop, hh=o, date=d)
                pend_entry = None
            pend_entry = None if mode == "nextopen" and k == 0 else pend_entry
            # partial bars
            def upd(p, b):
                if p is None:
                    return dict(o=b["o"], h=b["h"], l=b["l"], c=b["c"])
                return dict(o=p["o"], h=max(p["h"], b["h"]), l=min(p["l"], b["l"]), c=b["c"])
            pu = upd(pu, bu); ps = upd(ps, bs)
            if bp: pp = upd(pp, bp)
            # resting stop checked against this bar
            if pos:
                if bs["l"] <= pos["stop"]:
                    fill = min(bs["o"], pos["stop"])
                    cash += pos["sh"] * fill * (1 - COST); turn += pos["sh"] * fill
                    trades.append(fill / pos["entry"] - 1)
                    if pos["date"] == d: same_day += 1
                    pos = None
            evaluate = (mode == "live") or (mode == "eod" and k == len(hms) - 1) or (mode == "nextopen" and k == len(hms) - 1)
            if not evaluate:
                continue
            if mode == "nextopen":
                su = hu + [und_d[i]]; ssy = hs + [sym_d[i]]; sp = hp + [spy_d[i]]   # completed bar
            else:
                su = hu + [pu]; ssy = hs + [ps]; sp = hp + [pp if pp else spy_d[i]]
            c = su[-1]["c"]
            e_now, e_prev = broker.ema_pair(su, 20)
            adx = broker.adx(su, 14)
            mkt_ok = True
            if mk:
                se, _ = broker.ema_pair(sp, 20); mkt_ok = sp[-1]["c"] > se
            intact = c > e_now
            entry_ok = intact and e_now > e_prev and adx is not None and adx >= 25 and mkt_ok
            price = ssy[-1]["c"]
            a = broker.atr(ssy, 14)
            if pos:
                # trailing ratchet (live: hh from current price)
                pos["hh"] = max(pos["hh"], price)
                pos["stop"] = max(pos["stop"], pos["hh"] - 3.0 * a)
                if not intact:
                    if mode == "nextopen":
                        # exit at next open: approximate with next day's first bar open
                        pos["exit_next"] = True
                    else:
                        cash += pos["sh"] * price * (1 - COST); turn += pos["sh"] * price
                        trades.append(price / pos["entry"] - 1)
                        if pos["date"] == d: same_day += 1
                        pos = None
            elif entry_ok and not entered_today:
                eqn = cash
                sd = 1.5 * a
                sh = int(eqn * rp / 100.0 / sd) if sd > 0 else 0
                sh = min(sh, int(eqn * 0.5 / price))
                if mode == "nextopen":
                    pend_entry = (sh, price - sd, 0) if sh >= 1 else None
                elif sh >= 1:
                    cash -= sh * price * (1 + COST); turn += sh * price
                    pos = dict(sh=sh, entry=price, stop=price - sd, hh=price, date=d)
                    entered_today = True
        # nextopen mode: exit signalled at close fills at tomorrow's open
        if pos and pos.get("exit_next") and i + 1 < len(dates):
            o = ssym[dates[i + 1]][0]["o"]
            cash += pos["sh"] * o * (1 - COST); turn += pos["sh"] * o
            trades.append(o / pos["entry"] - 1); pos = None
        eq.append(cash + (pos["sh"] * sym_d[i]["c"] if pos else 0))
        gross.append((pos["sh"] * sym_d[i]["c"] / eq[-1]) if pos else 0.0)
    m = C.metrics(eq, trades, turn, sum(gross))
    return m, same_day, dates[LOOKBACK + 1:], eq


def main(argv):
    syms = argv or list(PAIRS)
    for sym in syms:
        und, rp, mk = PAIRS[sym]
        print("\n### %s (signal %s, risk %.0f%%, SPY confirm %s), 30-min SIP bars through the live indicator code" % (sym, und, rp, mk))
        print("| mode | window | return | CAGR | maxDD | Sharpe | MAR | trades | same-day round trips | turnover | exposure |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for mode in ("live", "eod", "nextopen"):
            m, sd, dts, eq = run(sym, und, rp, mk, mode)
            res = dict(eq=eq, gross=[0] * len(eq), trade_log=[], turn_log=[])
            print("| %s | %s..%s | %.0f%% | %.1f%% | %.1f%% | %.2f | %.2f | %d | %d | %.1fx | %.0f%% |" % (
                mode, dts[0], dts[-1], m["ret"] * 100, m["cagr"] * 100, m["mdd"] * 100, m["sharpe"], m["mar"],
                m["trades"], sd, m["turnover"], m["exposure"] * 100))
            for lab, lo, hi in (("2016-2020.08", C.BACK_LO, C.BACK_HI), ("2020.08-2026.06", C.TUNE_LO, C.TUNE_HI)):
                w = C.window(res, dts, lo, hi)
                print("| %s | %s | %.0f%% | %.1f%% | %.1f%% | %.2f | %.2f | | | | |" % (
                    "  " + mode, lab, w["ret"] * 100, w["cagr"] * 100, w["mdd"] * 100, w["sharpe"], w["mar"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
