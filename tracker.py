#!/usr/bin/env python3
"""
Live-vs-backtest forward tracker. The honest endgame: record each bot's REAL
forward results and compare them to (a) buy-and-hold the same symbol over the same
live window, and (b) what the backtest/walk-forward promised. Reality gets the vote.

  python3 tracker.py            # human report
  python3 tracker.py --snapshot # append a daily per-bot P&L snapshot to forward/track.csv
  python3 tracker.py --signal   # one-line-per-bot summary for a weekly Signal message
"""
from __future__ import annotations
import os, sys, json, datetime as dt
import broker, review

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "forward", "track.csv")
TRACK_START = "2026-06-10"   # bots went live ~2026-06-11

# Honest backtest/walk-forward expectations (incl. OOS verdicts) per deployed bot.
EXP = {
    "SOXL": "trend  | exp DD~25%, OOS Sh~0.9 (B&H beat OOS)",
    "MSTR": "trend  | exp DD~11%, OOS WEAKENS (Sh 0.23)",
    "PLTR": "trend  | exp DD~7%,  OOS Sh~1.1 (B&H beat OOS)",
    "TNA":  "trend  | exp DD~13%, OOS WEAKENS",
    "UPRO": "trend  | exp DD~13%, OOS WEAKENS",
    "TQQQ": "meanrev| exp DD~2%,  OOS FAILED (0 trades)",
    "LABU": "meanrev| exp DD~1%,  OOS FAILED (0 trades)",
}


def fifo(fills):
    """Match buys->sells FIFO. Returns (realized$, trade_returns[], open_qty, open_cost)."""
    fills = sorted(fills, key=lambda f: f.get("transaction_time", ""))
    lots = []; realized = 0.0; trades = []
    for f in fills:
        qty = float(f["qty"]); price = float(f["price"]); side = f.get("side", "")
        if side.startswith("buy"):
            lots.append([qty, price])
        else:
            rem = qty
            while rem > 1e-9 and lots:
                lqty, lprice = lots[0]
                m = min(rem, lqty)
                realized += m * (price - lprice)
                trades.append(price / lprice - 1)
                lqty -= m; rem -= m
                if lqty <= 1e-9:
                    lots.pop(0)
                else:
                    lots[0][0] = lqty
    return realized, trades, sum(q for q, _ in lots), sum(q * p for q, p in lots)


def bot_live(symbol, positions, key, sec):
    fills = broker.all_fills(symbol, key, sec, after=TRACK_START)
    realized, trades, open_qty, open_cost = fifo(fills)
    pos = positions.get(symbol)
    cur = float(pos["current_price"]) if pos else 0.0
    unreal = (open_qty * cur - open_cost) if open_qty else 0.0
    wins = sum(1 for t in trades if t > 0)
    first = min((f.get("transaction_time", "")[:10] for f in fills), default=None)
    return {
        "symbol": symbol, "total_pnl": realized + unreal, "realized": realized,
        "unreal": unreal, "trades": len(trades),
        "win_rate": (wins / len(trades)) if trades else None,
        "open_qty": open_qty, "first_fill": first, "fills": len(fills),
    }


def symbol_return(symbol, start, key, sec):
    """Buy-and-hold % of the symbol from `start` to now (the live benchmark)."""
    try:
        bars = broker.daily_bars(symbol, key, sec, lookback_days=40)
        bars = [b for b in bars if b["t"][:10] >= start]
        if len(bars) >= 2:
            return bars[-1]["c"] / bars[0]["c"] - 1
    except broker.AlpacaError:
        pass
    return None


def gather():
    key, sec = broker.load_creds()
    positions = {p["symbol"]: p for p in broker.get_positions(key, sec)}
    rows = []
    for cfg in review.all_configs():
        sym = review.symbol_of(cfg)
        live = bot_live(sym, positions, key, sec)
        live["bh"] = symbol_return(sym, live["first_fill"] or TRACK_START, key, sec)
        rows.append(live)
    return rows


def snapshot():
    os.makedirs(os.path.dirname(TRACK), exist_ok=True)
    today = dt.date.today().isoformat()
    new = not os.path.exists(TRACK)
    with open(TRACK, "a") as fh:
        if new:
            fh.write("date,symbol,total_pnl,realized,trades,open_qty\n")
        for r in gather():
            fh.write(f"{today},{r['symbol']},{r['total_pnl']:.2f},{r['realized']:.2f},"
                     f"{r['trades']},{r['open_qty']}\n")
    print(f"snapshot written to {TRACK}")


def report(for_signal=False):
    rows = gather()
    if for_signal:
        lines = ["📈 Forward tracker (live vs backtest)"]
        for r in rows:
            wr = f"{r['win_rate']*100:.0f}%" if r["win_rate"] is not None else "-"
            bh = f"{r['bh']*100:+.0f}%" if r["bh"] is not None else "?"
            lines.append(f"• {r['symbol']}: P&L ${r['total_pnl']:+,.0f} | {r['trades']}tr "
                         f"win {wr} | sym {bh}")
        return "\n".join(lines)
    print("=" * 74)
    print("  FORWARD TRACKER — live results vs buy-&-hold vs backtest expectation")
    print(f"  (since {TRACK_START}; bots are early — most metrics need weeks to matter)")
    print("=" * 74)
    print(f"  {'bot':6} {'live P&L':>9} {'trades':>7} {'win%':>6} {'sym B&H':>8}   backtest/OOS expectation")
    print("  " + "-" * 72)
    for r in rows:
        wr = f"{r['win_rate']*100:.0f}%" if r["win_rate"] is not None else "  -"
        bh = f"{r['bh']*100:+.0f}%" if r["bh"] is not None else "   ?"
        print(f"  {r['symbol']:6} ${r['total_pnl']:>+8,.0f} {r['trades']:>7} {wr:>6} "
              f"{bh:>8}   {EXP.get(r['symbol'], '')}")
    print("  " + "-" * 72)
    total = sum(r["total_pnl"] for r in rows)
    traded = [r for r in rows if r["trades"] > 0]
    print(f"  Combined live P&L ${total:+,.0f} across {len(rows)} bots "
          f"({len(traded)} have traded). 'sym B&H' = holding the symbol over the same "
          f"live window — the bot must beat THAT to justify itself.")


def main(argv):
    if "--snapshot" in argv:
        snapshot()
    elif "--send" in argv:
        import notify
        msg = report(for_signal=True)
        print("sent" if notify.send(msg) else "send failed")
    elif "--signal" in argv:
        print(report(for_signal=True))
    else:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
