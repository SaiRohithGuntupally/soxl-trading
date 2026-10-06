#!/usr/bin/env python3
"""
Fleet health from ANYWHERE (Mac, Pi, laptop) — read-only, uses only the Alpaca keys.

  python3 fleet_status.py            # human report
  python3 fleet_status.py --json     # machine-readable
  python3 fleet_status.py --stale-days 10
  python3 fleet_status.py --notify   # cron'd on the host: Signal-pages you when STALE or errored

Answers the question the Jul 8 -> Oct 6 2026 outage exposed: "is the fleet alive,
and if it isn't trading, is that a legitimate sit-out or a dead host?"
  - account, fleet-only positions and open orders (your manual positions are ignored)
  - last fleet order per bot and its age
  - each bot's CURRENT entry gate, computed from live bars with the bot's own config
  - heartbeat stamps if run on the host (heartbeat/<bot>) and the last push on origin
  - verdict: STALE when no fleet order for --stale-days while >=1 bot is enter-eligible.
Exit code: 0 healthy / 2 stale / 1 error — so it can be cron'd as a watchdog.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import subprocess
import sys

import broker
from bot import bars_lookback_days

HERE = os.path.dirname(os.path.abspath(__file__))


def bot_configs() -> list[tuple[str, dict]]:
    out = [(os.path.join(HERE, "config.json"), json.load(open(os.path.join(HERE, "config.json"))))]
    for p in sorted(glob.glob(os.path.join(HERE, "bots", "*", "config.json"))):
        out.append((p, json.load(open(p))))
    return out


def gate(cfg: dict, key: str, sec: str, spy_ok: bool, today: str) -> dict:
    """Same gate logic as bot.tick(), minus the trade. Keep in sync with bot.py."""
    und = cfg["underlying"]
    bars = broker.daily_bars(und, key, sec, lookback_days=bars_lookback_days(cfg), feed=cfg.get("feed", "iex"))
    close = bars[-1]["c"]
    strat = cfg.get("strategy", "trend")
    mkt_ok = spy_ok if cfg.get("mkt_confirm") else True
    ev_dates = cfg.get("event_dates") or []
    blk = int(cfg.get("event_block_days") or 0)
    t = dt.date.fromisoformat(today)
    event_block = any(0 <= (dt.date.fromisoformat(d) - t).days <= blk for d in ev_dates) if blk else False
    r = {"symbol": cfg["symbol"], "underlying": und, "strategy": strat, "close": round(close, 2),
         "bars": len(bars), "mkt_ok": mkt_ok, "event_block": event_block}
    if strat == "meanrev":
        rsi = broker.rsi(bars, int(cfg.get("rsi_len", 14)))
        regime = broker.sma(bars, int(cfg.get("regime_ma", 200)))
        r.update({"rsi": rsi and round(rsi, 1), "rsi_buy": cfg.get("rsi_buy", 30),
                  "regime": regime and round(regime, 2),
                  "regime_ok": regime is not None and close > regime})
        r["enter_ok"] = bool(rsi is not None and rsi < float(cfg.get("rsi_buy", 30))
                             and r["regime_ok"] and mkt_ok and not event_block)
        why = []
        if rsi is None or rsi >= float(cfg.get("rsi_buy", 30)): why.append("RSI %s >= %g" % (rsi and round(rsi), cfg.get("rsi_buy", 30)))
        if not r["regime_ok"]: why.append("below regime" if regime else "regime unavailable")
    else:
        ema, ema_prev = broker.ema_pair(bars, int(cfg["ema_len"]))
        adx = broker.adx(bars, 14) if cfg.get("chop_filter") else None
        chop_ok = (not cfg.get("chop_filter")) or (adx is not None and adx >= float(cfg["adx_min"]))
        r.update({"ema": round(ema, 2), "ema_rising": ema > ema_prev, "above_ema": close > ema,
                  "adx": adx and round(adx, 1), "adx_min": cfg.get("adx_min"), "chop_ok": chop_ok})
        r["enter_ok"] = bool(close > ema and ema > ema_prev and chop_ok and mkt_ok and not event_block)
        why = []
        if close <= ema: why.append("below EMA")
        elif ema <= ema_prev: why.append("EMA falling")
        if not chop_ok: why.append(f"ADX {adx and round(adx, 1)} < {cfg.get('adx_min')}")
    if not mkt_ok: why.append("SPY risk-off")
    if event_block: why.append("event block")
    r["why_not"] = ", ".join(why)
    return r


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    stale_days = 10
    if "--stale-days" in argv:
        stale_days = int(argv[argv.index("--stale-days") + 1])
    key, sec = broker.load_creds()
    cfgs = bot_configs()
    symbols = [c["symbol"] for _, c in cfgs]

    acct = broker.get_account(key, sec)
    clock = broker.get_clock(key, sec)
    today = clock["timestamp"][:10]
    positions = [p for p in broker.get_positions(key, sec) if p["symbol"] in symbols]
    open_orders = [o for o in broker.get_open_orders(key, sec) if o["symbol"] in symbols]

    # last order per fleet symbol (any status), 400 most recent orders is plenty
    orders = broker.api("GET", broker.TRADE_HOST, "/v2/orders?status=all&limit=500&direction=desc", key, sec)
    last_order: dict[str, dict] = {}
    for o in orders:
        if o["symbol"] in symbols and o["symbol"] not in last_order:
            last_order[o["symbol"]] = {"at": o["submitted_at"][:16], "side": o["side"],
                                       "type": o["type"], "qty": o["qty"], "status": o["status"]}
    newest = max((o["submitted_at"] for o in orders if o["symbol"] in symbols), default=None)
    now = dt.datetime.now(dt.timezone.utc)
    newest_age = (now - dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))).days if newest else None

    # SPY confirm once
    try:
        sb = broker.daily_bars("SPY", key, sec)
        e, _ = broker.ema_pair(sb, 20)
        spy_ok = sb[-1]["c"] > e
    except broker.AlpacaError:
        spy_ok = True
    gates = []
    for _, c in cfgs:
        try:
            gates.append(gate(c, key, sec, spy_ok, today))
        except Exception as ex:  # one bad symbol must not hide the rest
            gates.append({"symbol": c["symbol"], "error": str(ex), "enter_ok": False})

    # heartbeats (only meaningful on the host)
    beats = {}
    for p in glob.glob(os.path.join(HERE, "heartbeat", "*")):
        try:
            ts = open(p).read().split()[0]
            age_min = (now - dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60
            beats[os.path.basename(p)] = round(age_min)
        except Exception:
            pass

    # last push on origin (operator pushes only on change -> weak signal, but free)
    last_push = None
    try:
        subprocess.run(["git", "-C", HERE, "fetch", "-q", "origin"], timeout=20, check=False)
        last_push = subprocess.run(["git", "-C", HERE, "log", "-1", "--format=%ci %s", "origin/main"],
                                   capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        pass

    eligible = [g["symbol"] for g in gates if g.get("enter_ok")]
    holding = [p["symbol"] for p in positions]
    stale = bool(newest_age is not None and newest_age >= stale_days and eligible and not holding)
    verdict = ("STALE: no fleet order for %d days while %s pass their entry gates and nothing is held "
               "-> host is probably down (check the Pi: ./doctor.sh)" % (newest_age, "/".join(eligible))
               if stale else
               ("OK: holding " + ", ".join(holding)) if holding else
               ("OK: flat, all gates correctly closed" if not eligible else
                f"WATCH: {'/'.join(eligible)} eligible, no position yet (should fill on the next open-market tick)"))

    out = {"as_of": clock["timestamp"], "market_open": clock["is_open"],
           "equity": float(acct["equity"]), "fleet_positions": [
               {"symbol": p["symbol"], "qty": p["qty"], "unrealized_pl": float(p["unrealized_pl"])} for p in positions],
           "fleet_open_orders": len(open_orders), "last_order": last_order,
           "days_since_last_fleet_order": newest_age, "gates": gates, "heartbeat_age_min": beats,
           "origin_last_push": last_push, "eligible_now": eligible, "stale": stale, "verdict": verdict}
    if "--notify" in argv and stale:
        try:
            import notify
            notify.send("⚠️ fleet watchdog — " + verdict)
        except Exception:
            pass
    if as_json:
        print(json.dumps(out, indent=2, default=str))
        return 2 if stale else 0

    print(f"as of {out['as_of'][:16]}Z  market_open={out['market_open']}  equity ${out['equity']:,.0f}")
    pos_txt = ", ".join("%s %s (%+.0f)" % (p["symbol"], p["qty"], p["unrealized_pl"]) for p in out["fleet_positions"]) or "none"
    print(f"fleet positions: {pos_txt}   open fleet orders: {len(open_orders)}")
    print(f"last fleet order: {newest[:16] if newest else 'never'}  ({newest_age} days ago)")
    print()
    print(f"{'bot':5} {'strat':8} {'sig':5} {'close':>9} {'gate':6}  detail")
    for g in gates:
        if "error" in g:
            print(f"{g['symbol']:5} ERROR {g['error'][:70]}"); continue
        if g["strategy"] == "meanrev":
            d = f"RSI {g['rsi']} (<{g['rsi_buy']:g}), regime {'ok' if g['regime_ok'] else 'NO'} (SMA {g['regime']})"
        else:
            d = f"EMA {g['ema']} {'rising' if g['ema_rising'] else 'falling'}, ADX {g['adx']} (>={g['adx_min']:g})"
        lo = last_order.get(g["symbol"])
        d += f" | {g['why_not']}" if g["why_not"] else ""
        d += f" | last: {lo['at']} {lo['side']} {lo['status']}" if lo else " | last: never"
        print(f"{g['symbol']:5} {g['strategy']:8} {g['underlying']:5} {g['close']:9.2f} {'ENTER' if g['enter_ok'] else 'closed':6}  {d}")
    print()
    if beats:
        print("heartbeats (min ago): " + ", ".join(f"{k} {v}" for k, v in sorted(beats.items())))
    else:
        print("heartbeats: none here (only present on the host that runs the crons)")
    print(f"origin/main last push: {last_push or 'unknown'}")
    print()
    print("VERDICT:", verdict)
    return 2 if stale else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001 — a watchdog must report, not crash silently
        print("fleet_status error:", e)
        if "--notify" in sys.argv:
            try:
                import notify
                notify.send(f"⚠️ fleet watchdog could not run: {e}")
            except Exception:
                pass
        raise SystemExit(1)
