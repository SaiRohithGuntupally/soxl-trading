#!/usr/bin/env python3
"""
SOXL continuous paper-trading bot (Alpaca). One invocation = one "tick".

Strategy (intentionally simple + auditable):
  SIGNAL  : trade the UNDERLYING (SOXX). Long gate = price above a RISING EMA.
  SIZE    : 2% account risk, stop = stop_atr x SOXL's OWN ATR (right price scale).
  EXECUTE : server-side bracket order (entry + stop-loss + take-profit at tp_R).
  EXIT    : if holding and the trend gate fails, flatten.
  GUARD   : hard daily kill switch at max_daily_loss_pct -> flatten + halt for day.

Everything is logged to journal.jsonl (one JSON object per tick) and equity.csv
so the autonomous review pass has structured data to diagnose from.

Usage:
  python3 bot.py                 # one tick (trades for real on paper account)
  python3 bot.py --dry-run       # one tick, log decision but place NO orders
  python3 bot.py --loop 120      # tick every 120s until killed (local runtime)
  python3 bot.py --status        # print account/position/PnL summary, no trading
  python3 bot.py --flatten       # close everything now (manual kill)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

import broker
import notify
from position_size import compute

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
JOURNAL_PATH = os.path.join(HERE, "journal.jsonl")
EQUITY_CSV = os.path.join(HERE, "equity.csv")

DEFAULTS = {
    "underlying": "SOXX", "symbol": "SOXL", "risk_pct": 2.0, "stop_atr": 1.5,
    "tp_R": 2.0, "ema_len": 20, "atr_len": 14, "max_daily_loss_pct": 10.0,
    "max_position_pct": 50.0, "feed": "iex",
}
HARD_KILL_CEILING = 10.0  # reviewer may not set max_daily_loss_pct above this


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as fh:
            cfg.update({k: v for k, v in json.load(fh).items()
                        if not k.startswith("_")})
    # Enforce the kill-switch ceiling no matter what config says.
    if float(cfg["max_daily_loss_pct"]) > HARD_KILL_CEILING:
        cfg["max_daily_loss_pct"] = HARD_KILL_CEILING
    return cfg


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as fh:
            return json.load(fh)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=2)


def journal(entry: dict) -> None:
    with open(JOURNAL_PATH, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a") as fh:
        if new:
            fh.write("ts,equity,soxl_daily_pnl,daily_pnl_pct,action\n")
        fh.write(f"{entry['ts']},{entry.get('equity','')},"
                 f"{entry.get('soxl_daily_pnl','')},"
                 f"{entry.get('daily_pnl_pct','')},{entry.get('action','')}\n")


def find_position(positions, symbol):
    for p in positions:
        if p.get("symbol") == symbol:
            return p
    return None


def has_open_order(orders, symbol):
    return any(o.get("symbol") == symbol for o in orders)


def soxl_daily_pnl(cfg, today, pos_market_value, day_start_value, key, sec):
    """Bot's SOXL-only P&L for the day via the cashflow method:
        pnl = current_market_value + (today's sells - today's buys) - day_start_value
    Captures server-side bracket stop/TP fills the bot didn't place directly."""
    net_cashflow = 0.0
    for f in broker.todays_fills(cfg["symbol"], today, key, sec):
        px, qty = float(f["price"]), float(f["qty"])
        net_cashflow += (px * qty) if f.get("side", "").startswith("sell") else -(px * qty)
    return pos_market_value + net_cashflow - day_start_value


def tick(cfg, dry_run=False, log=print) -> dict:
    key, sec = broker.load_creds()
    acct = broker.get_account(key, sec)
    clock = broker.get_clock(key, sec)
    equity = float(acct["equity"])
    today = clock["timestamp"][:10]  # ET date from the API, not local clock

    # Fetch the SOXL position up front (needed for both P&L and the day baseline).
    positions = broker.get_positions(key, sec)
    pos = find_position(positions, cfg["symbol"])
    mv = float(pos["market_value"]) if pos else 0.0

    state = load_state()
    if state.get("date") != today:
        # New day: baseline equity (the bot's "allocated capital" denominator) and
        # the SOXL position value we carried in, so P&L is measured from here.
        state = {"date": today, "day_start_equity": equity,
                 "soxl_day_start_value": mv, "halted": False, "halt_reason": None}

    day_start = float(state["day_start_equity"])
    soxl_start_val = float(state.get("soxl_day_start_value", 0.0))

    # --- SOXL-ONLY daily P&L (ignores the rest of your account) ---
    pnl = soxl_daily_pnl(cfg, today, mv, soxl_start_val, key, sec)
    daily_pnl_pct = pnl / day_start * 100 if day_start else 0.0  # vs allocated capital

    rec = {
        "ts": clock["timestamp"], "equity": round(equity, 2),
        "day_start_equity": round(day_start, 2),
        "soxl_daily_pnl": round(pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 3),
        "market_open": clock["is_open"], "dry_run": dry_run,
        "has_position": bool(pos),
    }
    if pos:
        rec["qty"] = pos.get("qty")
        rec["soxl_market_value"] = round(mv, 2)
        rec["unrealized_intraday_pl"] = float(pos.get("unrealized_intraday_pl", 0))

    # --- HARD KILL SWITCH (non-negotiable) ---
    # Trips on the bot's OWN SOXL loss only, and flattens ONLY SOXL — never the
    # rest of the account.
    kill_level = -float(cfg["max_daily_loss_pct"]) / 100.0 * day_start
    if pnl <= kill_level:
        if not dry_run and pos:
            try:
                broker.close_position(cfg["symbol"], key, sec)
            except broker.AlpacaError as e:
                rec["flatten_error"] = str(e)
        state["halted"] = True
        state["halt_reason"] = (f"SOXL daily P&L ${pnl:.0f} "
                                f"<= -{cfg['max_daily_loss_pct']}% (${kill_level:.0f})")
        rec["action"] = "KILL_SWITCH"; rec["note"] = state["halt_reason"]
        save_state(state); journal(rec); log(f"KILL SWITCH: {state['halt_reason']}")
        notify.send(f"🚨 SOXL KILL SWITCH — {state['halt_reason']}. Flattened SOXL, "
                    f"halted for the day. Equity ${equity:,.0f}")
        return rec

    if state.get("halted"):
        rec["action"] = "HALTED"; rec["note"] = state.get("halt_reason")
        save_state(state); journal(rec); log("halted for the day; no trading")
        return rec

    if not clock["is_open"]:
        rec["action"] = "MARKET_CLOSED"
        save_state(state); journal(rec); log("market closed; no trading")
        return rec

    # --- signal from the underlying ---
    ubars = broker.daily_bars(cfg["underlying"], key, sec, feed=cfg["feed"])
    ema_now, ema_prev = broker.ema_pair(ubars, int(cfg["ema_len"]))
    u_close = ubars[-1]["c"]
    long_ok = (u_close > ema_now) and (ema_now > ema_prev)
    rec.update({"underlying_close": round(u_close, 2),
                "ema_now": round(ema_now, 2), "ema_prev": round(ema_prev, 2),
                "long_ok": long_ok})

    orders = broker.get_open_orders(key, sec)

    # --- decide ---
    if pos and not long_ok:
        if not dry_run:
            broker.close_position(cfg["symbol"], key, sec)
        rec["action"] = "CLOSE_TREND_BREAK"
        log(f"trend gate failed while holding -> close {cfg['symbol']}")
        notify.send(f"🔴 SOXL closed on trend break (SOXX {u_close:.2f} below "
                    f"EMA{int(cfg['ema_len'])} {ema_now:.2f}). Today P&L ${pnl:+,.0f}")

    elif (not pos) and (not has_open_order(orders, cfg["symbol"])) and long_ok:
        entry = broker.latest_price(cfg["symbol"], key, sec, feed=cfg["feed"])
        sbars = broker.daily_bars(cfg["symbol"], key, sec, feed=cfg["feed"])
        a = broker.atr(sbars, int(cfg["atr_len"]))
        plan = compute(equity, entry, float(cfg["risk_pct"]), a,
                       float(cfg["stop_atr"]), 1.0, None, None)
        shares = plan["shares"]
        notional = shares * entry
        max_notional = equity * float(cfg["max_position_pct"]) / 100.0
        rec.update({"entry": round(entry, 2), "atr": round(a, 3),
                    "planned_shares": shares, "stop": round(plan["stop_price"], 2)})
        if shares < 1:
            rec["action"] = "SKIP_SIZE_LT1"
            log("computed shares < 1; skip")
        elif notional > max_notional or notional > float(acct["buying_power"]):
            shares = int(min(max_notional, float(acct["buying_power"])) / entry)
            rec["planned_shares"] = shares
            if shares < 1:
                rec["action"] = "SKIP_NO_BUYING_POWER"
                log("not enough buying power; skip")
            else:
                rec["action"] = _place(cfg, shares, entry, plan, dry_run, key, sec, rec, log)
        else:
            rec["action"] = _place(cfg, shares, entry, plan, dry_run, key, sec, rec, log)
    else:
        rec["action"] = "HOLD" if pos else "FLAT_NO_SIGNAL"
        log(f"no action ({rec['action']})")

    save_state(state); journal(rec)
    return rec


def _place(cfg, shares, entry, plan, dry_run, key, sec, rec, log) -> str:
    stop = plan["stop_price"]
    tp = entry + (entry - stop) * float(cfg["tp_R"])
    if dry_run:
        log(f"[DRY] would BUY {shares} {cfg['symbol']} stop {stop:.2f} tp {tp:.2f}")
        rec["take_profit"] = round(tp, 2)
        return "DRY_OPEN"
    res = broker.submit_bracket(cfg["symbol"], shares, stop, tp, key, sec)
    rec["order_id"] = res.get("id")
    rec["take_profit"] = round(tp, 2)
    log(f"OPEN: BUY {shares} {cfg['symbol']} stop {stop:.2f} tp {tp:.2f} "
        f"id={res.get('id')}")
    notify.send(f"🟢 SOXL OPEN — bought {shares} @ ~${entry:.2f} | "
                f"stop ${stop:.2f} / target ${tp:.2f} (GTC)")
    return "OPEN"


def cmd_status(cfg):
    key, sec = broker.load_creds()
    acct = broker.get_account(key, sec)
    clock = broker.get_clock(key, sec)
    positions = broker.get_positions(key, sec)
    state = load_state()
    print(f"equity ${float(acct['equity']):,.2f} | buying power "
          f"${float(acct['buying_power']):,.2f} | market_open={clock['is_open']}")
    print(f"state: {json.dumps(state)}")
    if not positions:
        print("no open positions")
    for p in positions:
        print(f"  {p['symbol']} qty {p['qty']} @ {p['avg_entry_price']} "
              f"| uPL ${float(p['unrealized_pl']):.2f} "
              f"({float(p['unrealized_plpc'])*100:.2f}%)")


def main(argv):
    ap = argparse.ArgumentParser(description="SOXL continuous paper bot")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS",
                    help="tick repeatedly every N seconds (local runtime)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--flatten", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.status:
        cmd_status(cfg); return 0
    if args.flatten:
        # Close ONLY the bot's symbol, never the rest of the account.
        key, sec = broker.load_creds()
        positions = broker.get_positions(key, sec)
        if find_position(positions, cfg["symbol"]):
            print(broker.close_position(cfg["symbol"], key, sec))
        else:
            print(f"no {cfg['symbol']} position to close")
        return 0

    if args.loop:
        print(f"looping every {args.loop}s (Ctrl-C to stop)"
              f"{' [DRY]' if args.dry_run else ''}")
        while True:
            try:
                tick(cfg, dry_run=args.dry_run)
            except broker.AlpacaError as e:
                print(f"tick error (continuing): {e}", file=sys.stderr)
            time.sleep(args.loop)

    try:
        tick(cfg, dry_run=args.dry_run)
    except broker.AlpacaError as e:
        print(f"tick failed: {e}", file=sys.stderr)
        notify.send(f"⚠️ SOXL bot tick FAILED: {str(e)[:200]}")
        return 1
    except Exception as e:  # any unexpected failure should page, not vanish
        print(f"tick crashed: {e}", file=sys.stderr)
        notify.send(f"⚠️ SOXL bot CRASHED: {type(e).__name__}: {str(e)[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nstopped."); sys.exit(130)
