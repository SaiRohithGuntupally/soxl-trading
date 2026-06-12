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
import portfolio
from position_size import compute

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
# Runtime files live next to the active config, so each bot (root SOXL, or any
# bots/<TICKER>/config.json) is fully isolated. set_paths() points them at the dir.
STATE_PATH = os.path.join(HERE, "state.json")
JOURNAL_PATH = os.path.join(HERE, "journal.jsonl")
EQUITY_CSV = os.path.join(HERE, "equity.csv")


def set_paths(config_path):
    global STATE_PATH, JOURNAL_PATH, EQUITY_CSV
    d = os.path.dirname(os.path.abspath(config_path))
    STATE_PATH = os.path.join(d, "state.json")
    JOURNAL_PATH = os.path.join(d, "journal.jsonl")
    EQUITY_CSV = os.path.join(d, "equity.csv")

DEFAULTS = {
    "underlying": "SOXX", "symbol": "SOXL", "risk_pct": 2.0, "stop_atr": 1.5,
    "tp_R": 2.0, "ema_len": 20, "atr_len": 14, "max_daily_loss_pct": 10.0,
    "max_position_pct": 50.0, "feed": "iex",
    "chop_filter": True, "adx_min": 25.0,
    "trailing": True, "chand_atr": 3.0, "trail_tp_R": 8.0,
    "mkt_confirm": True, "mkt_symbol": "SPY", "mkt_ema": 20,
    "event_block_days": 1, "event_dates": [],
    "portfolio_max_loss_pct": 15.0,   # combined daily loss across all bots -> halt all
}
HARD_KILL_CEILING = 10.0  # reviewer may not set max_daily_loss_pct above this


def load_config(path=None) -> dict:
    cfg = dict(DEFAULTS)
    path = path or CONFIG_PATH
    if os.path.exists(path):
        with open(path) as fh:
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
        # Preserve trail_hh (highest-high since entry) across days while holding.
        state = {"date": today, "day_start_equity": equity,
                 "soxl_day_start_value": mv, "halted": False, "halt_reason": None,
                 "trail_hh": state.get("trail_hh") if pos else None}

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

    # --- PORTFOLIO circuit breaker: record this bot's P&L to the shared ledger,
    # then check the COMBINED daily loss across all bots. ---
    portfolio.record(cfg["symbol"], today, pnl, day_start)
    port_breach, port_total, port_base = portfolio.breached(
        today, float(cfg.get("portfolio_max_loss_pct", 0)))
    rec["portfolio_pnl"] = round(port_total, 2)

    # --- HARD KILL SWITCH (non-negotiable) ---
    # Trips on the bot's OWN symbol loss, OR a portfolio-wide breach. Flattens ONLY
    # this bot's symbol — never the rest of the account.
    kill_level = -float(cfg["max_daily_loss_pct"]) / 100.0 * day_start
    if pnl <= kill_level or port_breach:
        if not dry_run and pos:
            try:
                broker.close_position(cfg["symbol"], key, sec)
            except broker.AlpacaError as e:
                rec["flatten_error"] = str(e)
        sym = cfg["symbol"]
        state["halted"] = True
        if port_breach:
            state["halt_reason"] = (f"PORTFOLIO breaker: combined P&L ${port_total:.0f} "
                                    f"<= -{cfg.get('portfolio_max_loss_pct')}% (${port_base:.0f} base)")
        else:
            state["halt_reason"] = (f"{sym} daily P&L ${pnl:.0f} "
                                    f"<= -{cfg['max_daily_loss_pct']}% (${kill_level:.0f})")
        rec["action"] = "KILL_SWITCH"; rec["note"] = state["halt_reason"]
        save_state(state); journal(rec); log(f"KILL SWITCH: {state['halt_reason']}")
        notify.send(f"🚨 {sym} KILL SWITCH — {state['halt_reason']}. Flattened {sym}, "
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
    trend_intact = u_close > ema_now            # exit condition uses this only
    rising = ema_now > ema_prev

    # Chop filter: require a real trend (ADX) to ENTER (not to stay).
    adx_val = broker.adx(ubars, 14) if cfg.get("chop_filter") else None
    chop_ok = (not cfg.get("chop_filter")) or (adx_val is not None
                                               and adx_val >= float(cfg["adx_min"]))
    # Broad-market confirmation: don't go long leveraged semis when SPY is risk-off.
    mkt_ok = _mkt_confirm(cfg, key, sec)
    # Event gating: block NEW entries within event_block_days before a known event.
    event_block = _near_event(cfg, today)

    entry_ok = trend_intact and rising and chop_ok and mkt_ok and not event_block
    rec.update({"underlying_close": round(u_close, 2),
                "ema_now": round(ema_now, 2), "ema_prev": round(ema_prev, 2),
                "adx": round(adx_val, 1) if adx_val is not None else None,
                "mkt_ok": mkt_ok, "event_block": event_block, "entry_ok": entry_ok})

    orders = broker.get_open_orders(key, sec)

    # --- decide ---
    if pos and not trend_intact:
        if not dry_run:
            broker.close_position(cfg["symbol"], key, sec)
            broker.cancel_symbol_orders(cfg["symbol"], key, sec)
        state["trail_hh"] = None
        rec["action"] = "CLOSE_TREND_BREAK"
        log(f"trend gate failed while holding -> close {cfg['symbol']}")
        notify.send(f"🔴 SOXL closed on trend break (SOXX {u_close:.2f} below "
                    f"EMA{int(cfg['ema_len'])} {ema_now:.2f}). Today P&L ${pnl:+,.0f}")

    elif (not pos) and (not has_open_order(orders, cfg["symbol"])) and entry_ok:
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
                rec["action"] = _place(cfg, shares, entry, plan, dry_run, key, sec, rec, log, state)
        else:
            rec["action"] = _place(cfg, shares, entry, plan, dry_run, key, sec, rec, log, state)
    elif pos:
        # Holding with the trend intact -> ratchet the trailing stop up.
        if cfg.get("trailing") and not dry_run:
            _manage_trailing(cfg, pos, key, sec, rec, log, state)
        rec["action"] = "HOLD"
        log(f"hold ({cfg['symbol']})")
    else:
        rec["action"] = "FLAT_NO_SIGNAL"
        log(f"no action ({rec['action']})")

    save_state(state); journal(rec)
    return rec


def _place(cfg, shares, entry, plan, dry_run, key, sec, rec, log, state) -> str:
    stop = plan["stop_price"]
    # When trailing, set the bracket TP far out so the trailing stop (not a fixed
    # target) governs the exit — matches the backtested "let winners run" rule.
    tp_mult = float(cfg["trail_tp_R"]) if cfg.get("trailing") else float(cfg["tp_R"])
    tp = entry + (entry - stop) * tp_mult
    if dry_run:
        log(f"[DRY] would BUY {shares} {cfg['symbol']} stop {stop:.2f} tp {tp:.2f}")
        rec["take_profit"] = round(tp, 2)
        return "DRY_OPEN"
    res = broker.submit_bracket(cfg["symbol"], shares, stop, tp, key, sec)
    rec["order_id"] = res.get("id")
    rec["take_profit"] = round(tp, 2)
    state["trail_hh"] = entry            # seed trailing high-water mark at entry
    log(f"OPEN: BUY {shares} {cfg['symbol']} stop {stop:.2f} tp {tp:.2f} "
        f"id={res.get('id')}")
    notify.send(f"🟢 SOXL OPEN — bought {shares} @ ~${entry:.2f} | "
                f"stop ${stop:.2f} / target ${tp:.2f} (GTC)")
    return "OPEN"


def _mkt_confirm(cfg, key, sec) -> bool:
    """True if broad-market confirmation passes (SPY > its EMA) or is disabled.
    Fail-OPEN on data error so a SPY data hiccup never silently halts trading."""
    if not cfg.get("mkt_confirm"):
        return True
    try:
        b = broker.daily_bars(cfg.get("mkt_symbol", "SPY"), key, sec, feed=cfg["feed"])
        e_now, _ = broker.ema_pair(b, int(cfg.get("mkt_ema", 20)))
        return b[-1]["c"] > e_now
    except broker.AlpacaError:
        return True


def _near_event(cfg, today: str) -> bool:
    """True if `today` is within event_block_days BEFORE any configured event."""
    dates = cfg.get("event_dates") or []
    block = int(cfg.get("event_block_days", 0))
    if not dates or block <= 0:
        return False
    import datetime as _dt
    try:
        t = _dt.date.fromisoformat(today)
    except ValueError:
        return False
    for d in dates:
        try:
            ed = _dt.date.fromisoformat(d)
        except (ValueError, TypeError):
            continue
        if 0 <= (ed - t).days <= block:
            return True
    return False


def _manage_trailing(cfg, pos, key, sec, rec, log, state):
    """Ratchet the protective stop up toward a chandelier level (never down).
    Best-effort: trailing must never crash a tick."""
    try:
        atr_now = broker.atr(broker.daily_bars(cfg["symbol"], key, sec,
                                               feed=cfg["feed"]), int(cfg["atr_len"]))
        price = float(pos.get("current_price") or pos.get("avg_entry_price"))
        hh = max(float(state.get("trail_hh") or pos["avg_entry_price"]), price)
        state["trail_hh"] = hh
        chandelier = hh - float(cfg["chand_atr"]) * atr_now
        so = broker.open_stop_order(cfg["symbol"], key, sec)
        if not so:
            return
        cur_stop = float(so.get("stop_price") or 0)
        rec["trail_hh"] = round(hh, 2)
        rec["chandelier"] = round(chandelier, 2)
        if chandelier > cur_stop + 0.01:        # only ever move the stop UP
            broker.replace_order(so["id"], key, sec, stop_price=round(chandelier, 2))
            rec["trail_moved_to"] = round(chandelier, 2)
            log(f"trail stop {cur_stop:.2f} -> {chandelier:.2f}")
    except (broker.AlpacaError, KeyError, ValueError, TypeError) as e:
        rec["trail_error"] = str(e)[:120]


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
    ap = argparse.ArgumentParser(description="continuous paper trading bot")
    ap.add_argument("--config", help="path to a bot config.json (default: root config.json)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS",
                    help="tick repeatedly every N seconds (local runtime)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--flatten", action="store_true")
    args = ap.parse_args(argv)
    if args.config:
        set_paths(args.config)             # isolate runtime files next to the config
    cfg = load_config(args.config)

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

    sym = cfg.get("symbol", "?")
    try:
        tick(cfg, dry_run=args.dry_run)
    except broker.AlpacaError as e:
        print(f"tick failed: {e}", file=sys.stderr)
        notify.send(f"⚠️ {sym} bot tick FAILED: {str(e)[:200]}")
        return 1
    except Exception as e:  # any unexpected failure should page, not vanish
        print(f"tick crashed: {e}", file=sys.stderr)
        notify.send(f"⚠️ {sym} bot CRASHED: {type(e).__name__}: {str(e)[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nstopped."); sys.exit(130)
