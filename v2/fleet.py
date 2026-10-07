#!/usr/bin/env python3
"""
v2 fleet CLI.

  python3 fleet.py tick                # one tick (cron this every 15 min, weekdays)
  python3 fleet.py tick --dry-run      # decide but place no orders
  python3 fleet.py tick --decide       # force a decision tick now (testing; market must be open to fill)
  python3 fleet.py status              # account, core, bots, gates, last decisions, verdict
  python3 fleet.py flatten [SYM ...]   # close bot positions (never the core) and halt them today
  python3 fleet.py backtest            # reproduce the validated numbers for THIS config vs SPY and v1
  python3 fleet.py install-cron        # replace v1 crons with the single v2 line (+ watchdog)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine  # noqa: E402
import ledger as ledger_mod  # noqa: E402


def cmd_status(cfg, as_json=False) -> int:
    from engine import broker
    E = engine.Engine(cfg, dry_run=True)
    acct = E.b.get_account(E.key, E.sec); clock = E.b.get_clock(E.key, E.sec)
    positions = E.b.get_positions(E.key, E.sec); orders = E.b.get_open_orders(E.key, E.sec)
    equity = float(acct["equity"]); today = clock["timestamp"][:10]
    core = cfg["core"]; cp = E._pos(positions, core["symbol"])
    core_mv = float(cp["market_value"]) if cp else 0.0
    size_eq = equity - core_mv
    day = E.L.get("day", {}) or {}
    rows = []
    for b in cfg["bots"]:
        s = b["symbol"]; p = E._pos(positions, s); st = E._bot_state(s)
        try:
            g = E.gate(b, today)
        except Exception as e:  # noqa: BLE001
            g = {"error": str(e)[:80]}
        stop = next((o.get("stop_price") for o in orders if o.get("symbol") == s and (o.get("type") or "").startswith("stop")), None)
        rows.append({"symbol": s, "qty": p.get("qty") if p else 0, "mv": round(float(p["market_value"]), 2) if p else 0.0,
                     "upl": round(float(p["unrealized_pl"]), 2) if p else 0.0, "stop": stop,
                     "halted": bool(st.get("halted")), "gate": g})
    last = E.L.last_tick(); decs = E.L.recent_decisions(10)
    v1 = E._v1_alive()
    hb = os.path.join(HERE, "heartbeat", "fleet")
    hb_age = round((dt.datetime.now().timestamp() - os.path.getmtime(hb)) / 60) if os.path.exists(hb) else None
    verdict = []
    if v1: verdict.append(f"v1 bots still ticking ({', '.join(v1)}): v2 will REFUSE to trade until install-cron retires them")
    if hb_age is None: verdict.append("no v2 heartbeat yet on this machine")
    elif hb_age > 45 and clock.get("is_open"): verdict.append(f"v2 heartbeat is {hb_age} min old during market hours: cron dead?")
    if core["fraction"] > 0 and equity and abs(core_mv / equity - core["fraction"]) > core["rebalance_band"]:
        verdict.append(f"core at {core_mv/equity*100:.0f}% vs target {core['fraction']*100:.0f}% (rebalances at next decision tick)")
    if day.get("halted_all"): verdict.append("PORTFOLIO BREAKER tripped today: " + str(day.get("halt_reason")))
    out = {"as_of": clock["timestamp"], "market_open": clock["is_open"], "equity": equity, "core_mv": core_mv,
           "core_pct": round(core_mv / equity * 100, 1) if equity else 0, "size_equity": size_eq,
           "bots": rows, "day": day, "last_tick": last, "recent_decisions": decs, "heartbeat_age_min": hb_age,
           "v1_alive": v1, "verdict": verdict or ["OK"]}
    if as_json:
        print(json.dumps(out, indent=2, default=str)); return 0
    print(f"as of {clock['timestamp'][:16]}  open={clock['is_open']}  equity ${equity:,.0f}  "
          f"core {core['symbol']} ${core_mv:,.0f} ({out['core_pct']}%, target {core['fraction']*100:.0f}%)  non-core ${size_eq:,.0f}")
    print(f"{'bot':5} {'qty':>5} {'mv':>10} {'upl':>8} {'stop':>8} {'halt':5}  gate")
    for r in rows:
        g = r["gate"]
        gtxt = g.get("error") or (f"{'ENTER' if g['enter'] else ('EXIT' if g['exit'] else 'hold/flat')}: close {g['close']} ema {g['ema']} "
                                  f"{'rising' if g['ema_rising'] else 'falling'} adx {g['adx']} mkt {'ok' if g['mkt_ok'] else 'OFF'}")
        print(f"{r['symbol']:5} {str(r['qty']):>5} {r['mv']:>10,.0f} {r['upl']:>8,.0f} {str(r['stop'] or '-'):>8} {str(r['halted']):5}  {gtxt}")
    print(f"last tick: {last['ts'] if last else 'never'} {last['mode'] if last else ''}   decided today: {day.get('decided')}   heartbeat {hb_age} min")
    for d in decs[:6]:
        print(f"  {d['ts'][:16]} {d['symbol']:5} {d['action']:15} {str(d['detail'].get('note') or '')[:70]}")
    print("VERDICT: " + "; ".join(out["verdict"]))
    return 0


def cmd_backtest() -> int:
    return subprocess.call([sys.executable, os.path.join(HERE, "backtest.py")])


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd, rest = argv[0], argv[1:]
    cfg = engine.load_config()
    if cmd == "tick":
        E = engine.Engine(cfg, dry_run="--dry-run" in rest, force="--force" in rest)
        rc = 0
        try:
            E.tick(force_decide="--decide" in rest)
        except Exception as e:  # noqa: BLE001
            engine.log(f"tick failed: {e}"); rc = 1
            engine._notify(f"❌ v2 tick failed: {str(e)[:200]}")
        E._heartbeat(rc)
        return rc
    if cmd == "status":
        return cmd_status(cfg, as_json="--json" in rest)
    if cmd == "flatten":
        engine.Engine(cfg).flatten(rest or None); return 0
    if cmd == "backtest":
        return cmd_backtest()
    if cmd == "install-cron":
        return subprocess.call([os.path.join(HERE, "install_cron.sh")] + rest)
    print(__doc__); return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
