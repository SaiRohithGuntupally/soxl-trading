#!/usr/bin/env python3
"""
Test the SOXL toolkit against the Alpaca PAPER API (stdlib only, no pip installs).

What it does (read-only by default):
  1. Verifies credentials   -> GET /v2/account, /v2/clock
  2. Pulls your paper equity -> feeds the position sizer with REAL account size
  3. Pulls SOXX daily bars   -> computes ATR(14) + EMA(20) (the checklist signal)
  4. Pulls latest SOXL trade -> uses it as the entry price
  5. Prints the position-size plan from position_size.py

Place a real PAPER order only if you pass --place (off by default). It submits a
bracket order (entry + stop-loss + take-profit) sized by the 2% rule.

Credentials (paper keys from https://app.alpaca.markets, "Paper" mode):
  Preferred — environment variables:
      export APCA_API_KEY_ID=...        (paper key id)
      export APCA_API_SECRET_KEY=...    (paper secret)
  Or a .env file next to this script (KEY=VALUE lines). See .env.example.

Usage:
  python3 alpaca_test.py                # read-only checks + sizing plan
  python3 alpaca_test.py --risk-pct 1   # size at 1% risk instead of 2%
  python3 alpaca_test.py --place        # actually submit a paper bracket order
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from position_size import compute, report

TRADE_HOST = "https://paper-api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------- credentials ----------------------------------------------------

def load_creds() -> tuple[str, str]:
    key = os.environ.get("APCA_API_KEY_ID")
    sec = os.environ.get("APCA_API_SECRET_KEY")
    if not (key and sec):
        env_path = os.path.join(HERE, ".env")
        if os.path.exists(env_path):
            with open(env_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    if k.strip() == "APCA_API_KEY_ID" and not key:
                        key = v
                    elif k.strip() == "APCA_API_SECRET_KEY" and not sec:
                        sec = v
    if not (key and sec):
        sys.exit(
            "ERROR: no Alpaca credentials found.\n"
            "  Set APCA_API_KEY_ID and APCA_API_SECRET_KEY as env vars,\n"
            "  or create soxl-trading/.env (see .env.example)."
        )
    return key, sec


# ---------- http -----------------------------------------------------------

def api(method: str, host: str, path: str, key: str, sec: str,
        body: dict | None = None) -> dict:
    url = host + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("APCA-API-KEY-ID", key)
    req.add_header("APCA-API-SECRET-KEY", sec)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"network error on {path}: {e.reason}")


# ---------- indicators (stdlib math) --------------------------------------

def atr(bars: list[dict], period: int = 14) -> float:
    trs = []
    prev_close = None
    for b in bars:
        h, l, c = b["h"], b["l"], b["c"]
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) < period:
        raise SystemExit(f"need >= {period} bars for ATR, got {len(trs)}")
    return sum(trs[-period:]) / period


def ema(bars: list[dict], period: int = 20) -> tuple[float, float]:
    """Return (current EMA, EMA from one bar ago) so we can read the slope."""
    closes = [b["c"] for b in bars]
    if len(closes) < period:
        raise SystemExit(f"need >= {period} bars for EMA, got {len(closes)}")
    k = 2 / (period + 1)
    e = sum(closes[:period]) / period  # seed with SMA
    series = [e]
    for c in closes[period:]:
        e = c * k + e * (1 - k)
        series.append(e)
    return series[-1], (series[-2] if len(series) >= 2 else series[-1])


# ---------- main flow ------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Test SOXL toolkit vs Alpaca paper API")
    ap.add_argument("--risk-pct", type=float, default=2.0)
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--trail-atr", type=float, default=1.0)
    ap.add_argument("--underlying", default="SOXX", help="signal symbol")
    ap.add_argument("--symbol", default="SOXL", help="symbol to trade")
    ap.add_argument("--place", action="store_true",
                    help="actually submit a PAPER bracket order")
    args = ap.parse_args(argv)

    key, sec = load_creds()
    print("=" * 60)
    print("  ALPACA PAPER API TEST")
    print("=" * 60)

    # 1. auth + account
    acct = api("GET", TRADE_HOST, "/v2/account", key, sec)
    clock = api("GET", TRADE_HOST, "/v2/clock", key, sec)
    equity = float(acct["equity"])
    print(f"  [1] Auth OK. account {acct['account_number']} ({acct['status']})")
    print(f"      Equity ${equity:,.2f} | buying power ${float(acct['buying_power']):,.2f}")
    print(f"      Market open: {clock['is_open']}  (next open {clock['next_open']})")

    # 2. underlying bars -> ATR + EMA  (need ~3 months to seed EMA20 + ATR14)
    import datetime as _dt
    start = (_dt.date.today() - _dt.timedelta(days=120)).isoformat()
    bars_path = (f"/v2/stocks/{args.underlying}/bars"
                 f"?timeframe=1Day&start={start}&limit=200"
                 f"&adjustment=raw&feed=iex")
    bj = api("GET", DATA_HOST, bars_path, key, sec)
    bars = bj.get("bars") or []
    if not bars:
        raise SystemExit(f"no bars returned for {args.underlying}: {bj}")
    ema_now, ema_prev = ema(bars, 20)            # trend signal: underlying EMA only
    last_close = bars[-1]["c"]
    slope = "rising" if ema_now > ema_prev else "falling/flat"
    above = last_close > ema_now
    print(f"  [2] {args.underlying} (signal): close {last_close:.2f} | "
          f"EMA20 {ema_now:.2f} ({slope})")
    long_ok = above and ema_now > ema_prev
    print(f"      Checklist trend gate (long): "
          f"{'PASS' if long_ok else 'FAIL — stand aside'}")

    # 3. SOXL's OWN bars -> ATR for stops/sizing (must match the traded symbol's
    #    price scale, NOT the underlying's), plus latest trade for entry.
    sbars_path = (f"/v2/stocks/{args.symbol}/bars"
                  f"?timeframe=1Day&start={start}&limit=200"
                  f"&adjustment=raw&feed=iex")
    sbj = api("GET", DATA_HOST, sbars_path, key, sec)
    sbars = sbj.get("bars") or []
    if not sbars:
        raise SystemExit(f"no bars returned for {args.symbol}: {sbj}")
    a = atr(sbars, 14)
    tr = api("GET", DATA_HOST,
             f"/v2/stocks/{args.symbol}/trades/latest?feed=iex", key, sec)
    entry = float(tr["trade"]["p"])
    print(f"  [3] {args.symbol}: last ${entry:.2f} | own ATR14 {a:.2f} "
          f"({a / entry * 100:.1f}% of price)")

    # 4. position sizing with REAL equity + SOXL's own ATR
    p = compute(equity, entry, args.risk_pct, a, args.stop_atr,
                args.trail_atr, None, None)
    print("  [4] Sizing plan:\n")
    print(report(p, equity, entry, args.risk_pct))

    # 5. optional paper order
    if not args.place:
        print("\n  (read-only: pass --place to submit this as a PAPER bracket order)")
        return 0

    if p["shares"] < 1:
        raise SystemExit("computed shares < 1; nothing to place.")
    if not long_ok:
        print("\n  WARNING: trend gate FAILED — placing anyway because you asked, "
              "but this is exactly the setup the checklist says to skip.")
    take_profit = round(entry + (entry - p["stop_price"]) * 2, 2)  # 2R target
    order = {
        "symbol": args.symbol,
        "qty": str(p["shares"]),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {"limit_price": take_profit},
        "stop_loss": {"stop_price": round(p["stop_price"], 2)},
    }
    print(f"\n  [5] Submitting PAPER bracket order: BUY {p['shares']} {args.symbol} "
          f"| stop {order['stop_loss']['stop_price']} | tp {take_profit}")
    res = api("POST", TRADE_HOST, "/v2/orders", key, sec, body=order)
    print(f"      Order accepted: id={res['id']} status={res['status']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\naborted.")
        sys.exit(130)
