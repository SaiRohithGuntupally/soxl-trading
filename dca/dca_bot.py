#!/usr/bin/env python3
"""
SOXL DCA accumulator — a SEPARATE project from the trend bots.

Strategy (validated in ../dca.py): buy a base tranche, then a safety order each time
price drops `drop_pct` below the last buy. ACCUMULATE AND HOLD — never sells, no
kill switch (by design: it rides the drawdown for the recovery; backtest showed ~88%
max drawdown but it slightly beat buy-and-hold over a full cycle). Bounded only by
max_safety_orders + allocated_capital so it can't consume the whole account.

CRITICAL: run this on its OWN Alpaca paper account (its own dca/.env), NOT the trend
bots' account — both would fight over the single SOXL position otherwise.

Usage:
  python3 dca_bot.py --status
  python3 dca_bot.py --dry-run
  python3 dca_bot.py            # one live tick (places real paper orders)
"""
from __future__ import annotations
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import broker  # noqa: E402  (reuse the Alpaca client + creds loader)
try:
    import notify  # noqa: E402  (Signal alerts, account-agnostic)
except Exception:
    notify = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
STATE = os.path.join(HERE, "state.json")
JOURNAL = os.path.join(HERE, "journal.jsonl")

DEFAULTS = {"symbol": "SOXL", "drop_pct": 8.0, "max_safety_orders": 8,
            "allocated_capital": 20000.0, "feed": "iex"}


def load_creds():
    """Prefer this project's own dca/.env (separate paper account); fall back to the
    repo-root .env only so --dry-run works before the 2nd account is wired up."""
    for env in (os.path.join(HERE, ".env"), os.path.join(broker.HERE, ".env")):
        if os.path.exists(env):
            k = s = None
            for line in open(env):
                line = line.strip()
                if line.startswith("APCA_API_KEY_ID="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("APCA_API_SECRET_KEY="):
                    s = line.split("=", 1)[1].strip().strip('"').strip("'")
            if k and s:
                return k, s, ("dca" if env.startswith(HERE) else "ROOT-fallback")
    raise broker.AlpacaError("no creds (create dca/.env with the 2nd paper account)")


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        cfg.update({k: v for k, v in json.load(open(CONFIG)).items()
                    if not k.startswith("_")})
    return cfg


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save_state(st):
    json.dump(st, open(STATE, "w"), indent=2)


def journal(rec):
    with open(JOURNAL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def tick(cfg, key, sec, dry_run=False, log=print):
    sym = cfg["symbol"]
    acct = broker.get_account(key, sec)
    clock = broker.get_clock(key, sec)
    bp = float(acct["buying_power"])
    positions = broker.get_positions(key, sec)
    pos = next((p for p in positions if p["symbol"] == sym), None)
    price = broker.latest_price(sym, key, sec, feed=cfg["feed"])
    st = load_state()
    n_orders = int(st.get("n_orders", 0))
    last_buy = st.get("last_buy_price")
    tranche = float(cfg["allocated_capital"]) / (int(cfg["max_safety_orders"]) + 1)
    deployed = float(pos["cost_basis"]) if pos else 0.0

    rec = {"ts": clock["timestamp"], "symbol": sym, "price": round(price, 2),
           "n_orders": n_orders, "last_buy": last_buy,
           "deployed": round(deployed, 2), "market_open": clock["is_open"],
           "dry_run": dry_run}

    def do_buy(reason):
        shares = int(min(tranche, bp, float(cfg["allocated_capital"]) - deployed) / price)
        if shares < 1:
            rec["action"] = "SKIP_NO_CAPACITY"; log("no capacity for a tranche"); return
        rec.update({"buy_shares": shares, "reason": reason})
        if dry_run:
            rec["action"] = "DRY_BUY"; log(f"[DRY] would BUY {shares} {sym} @ ~{price:.2f} ({reason})"); return
        broker.api("POST", broker.TRADE_HOST, "/v2/orders", key, sec, body={
            "symbol": sym, "qty": str(shares), "side": "buy",
            "type": "market", "time_in_force": "day"})
        st["n_orders"] = n_orders + 1
        st["last_buy_price"] = price
        save_state(st)
        rec["action"] = "BUY"
        log(f"BUY {shares} {sym} @ ~{price:.2f} ({reason}); order #{n_orders+1}")
        if notify:
            notify.send(f"🟢 SOXL-DCA buy #{n_orders+1}: {shares} @ ~${price:.2f} "
                        f"({reason}). Deployed ~${deployed+shares*price:,.0f}/"
                        f"${cfg['allocated_capital']:,.0f}")

    if not clock["is_open"]:
        rec["action"] = "MARKET_CLOSED"; log("market closed")
    elif n_orders == 0 and not pos:
        do_buy("base order")
    elif last_buy and price <= last_buy * (1 - float(cfg["drop_pct"]) / 100.0) \
            and n_orders <= int(cfg["max_safety_orders"]) \
            and deployed < float(cfg["allocated_capital"]):
        do_buy(f"dip {(price/last_buy-1)*100:.1f}% below last buy")
    else:
        rec["action"] = "HOLD"
        log(f"hold ({n_orders} orders, deployed ${deployed:,.0f}, "
            f"next buy <= ${ (last_buy*(1-cfg['drop_pct']/100)) if last_buy else 0:.2f})")
    journal(rec)
    return rec


def main(argv):
    ap = argparse.ArgumentParser(description="SOXL DCA accumulator (separate project)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()
    key, sec, src = load_creds()
    if args.status:
        acct = broker.get_account(key, sec)
        pos = next((p for p in broker.get_positions(key, sec)
                    if p["symbol"] == cfg["symbol"]), None)
        print(f"creds: {src} | account {acct['account_number']} | equity ${float(acct['equity']):,.2f}")
        print(f"state: {json.dumps(load_state())}")
        print(f"SOXL: {pos['qty']+' sh, uPL $'+pos['unrealized_pl'] if pos else 'flat'}")
        if src == "ROOT-fallback":
            print("WARNING: using the trend bots' account — create dca/.env with a "
                  "SEPARATE paper account before going live (position collision otherwise).")
        return 0
    try:
        tick(cfg, key, sec, dry_run=args.dry_run)
    except Exception as e:
        print(f"tick failed: {e}", file=sys.stderr)
        if notify:
            notify.send(f"⚠️ SOXL-DCA bot failed: {str(e)[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
