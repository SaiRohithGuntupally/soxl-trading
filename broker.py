"""
Thin Alpaca paper-API client + indicators, shared by bot.py and tooling.
Stdlib only. Raises AlpacaError on failure (callers decide how to handle) so a
long-running loop never dies on a transient HTTP blip.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request

TRADE_HOST = "https://paper-api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"
HERE = os.path.dirname(os.path.abspath(__file__))


class AlpacaError(RuntimeError):
    pass


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
        raise AlpacaError("no Alpaca credentials (env vars or .env)")
    return key, sec


def api(method: str, host: str, path: str, key: str, sec: str,
        body: dict | None = None, timeout: int = 15) -> dict | list:
    url = host + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("APCA-API-KEY-ID", key)
    req.add_header("APCA-API-SECRET-KEY", sec)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise AlpacaError(f"HTTP {e.code} {method} {path}: {detail[:300]}")
    except urllib.error.URLError as e:
        raise AlpacaError(f"network error {method} {path}: {e.reason}")


# ---- convenience wrappers -------------------------------------------------

def get_account(key, sec):
    return api("GET", TRADE_HOST, "/v2/account", key, sec)


def get_clock(key, sec):
    return api("GET", TRADE_HOST, "/v2/clock", key, sec)


def get_positions(key, sec):
    return api("GET", TRADE_HOST, "/v2/positions", key, sec)


def get_open_orders(key, sec):
    return api("GET", TRADE_HOST, "/v2/orders?status=open&limit=100", key, sec)


def daily_bars(symbol, key, sec, lookback_days=160, feed="iex"):
    start = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    path = (f"/v2/stocks/{symbol}/bars?timeframe=1Day&start={start}"
            f"&limit=300&adjustment=raw&feed={feed}")
    j = api("GET", DATA_HOST, path, key, sec)
    bars = j.get("bars") or []
    if not bars:
        raise AlpacaError(f"no daily bars for {symbol}: {j}")
    return bars


def latest_price(symbol, key, sec, feed="iex"):
    j = api("GET", DATA_HOST,
            f"/v2/stocks/{symbol}/trades/latest?feed={feed}", key, sec)
    return float(j["trade"]["p"])


def _et_date(ts: str) -> str:
    """ET calendar date of an RFC3339 timestamp (orders report UTC, e.g.
    '2026-07-06T13:45:12.733159Z'). Falls back to the raw date on any parse/tz
    problem; regular-hours fills (13:30-20:00 UTC) share the date either way."""
    try:
        from zoneinfo import ZoneInfo
        s = ts.replace("Z", "+00:00")
        if "." in s:                      # trim sub-microsecond digits for fromisoformat
            head, tail = s.split(".", 1)
            frac = tail[:6].ljust(6, "0") if tail[0].isdigit() else ""
            tz = tail.lstrip("0123456789")
            s = f"{head}.{frac}{tz}" if frac else f"{head}{tz}"
        d = dt.datetime.fromisoformat(s)
        return d.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (ts or "")[:10]


def todays_fills(symbol, date, key, sec):
    """All FILL activities for `symbol` on `date` (YYYY-MM-DD), reconciled against
    the order book so the cashflow P&L cannot see a position vanish without its
    sale: if a closed order for `symbol` shows `filled_at` today but its filled_qty
    is not (fully) present in the activities feed yet (activities can lag the
    position/orders endpoints by a tick), the missing quantity is synthesized at
    the order's filled_avg_price. Without this, a stopped-out position whose FILL
    activity has not landed yet reads as pnl = -day_start_value, which can falsely
    trip the symbol kill switch AND the shared portfolio breaker for every bot."""
    acts = api("GET", TRADE_HOST,
               f"/v2/account/activities?activity_types=FILL&date={date}", key, sec)
    fills = [a for a in acts if a.get("symbol") == symbol] if isinstance(acts, list) else []
    try:
        seen = {}
        for a in fills:
            oid = a.get("order_id")
            if oid:
                seen[oid] = seen.get(oid, 0.0) + float(a.get("qty") or 0)
        closed = api("GET", TRADE_HOST,
                     f"/v2/orders?status=closed&symbols={symbol}&limit=500", key, sec)
        for o in (closed if isinstance(closed, list) else []):
            if o.get("symbol") != symbol or not o.get("filled_at"):
                continue
            if _et_date(o["filled_at"]) != date:
                continue
            fq = float(o.get("filled_qty") or 0)
            px = float(o.get("filled_avg_price") or 0)
            missing = fq - seen.get(o.get("id"), 0.0)
            if missing > 1e-9 and px > 0:
                fills.append({"symbol": symbol, "side": o.get("side", ""), "qty": str(missing),
                              "price": str(px), "order_id": o.get("id"),
                              "transaction_time": o["filled_at"], "synthetic": True})
    except (AlpacaError, ValueError, TypeError, KeyError):
        pass                              # reconciliation is best-effort; activities stand
    return fills


def all_fills(symbol, key, sec, after="2026-06-01"):
    """Every FILL for `symbol` since `after` (paginated). The bot's full record."""
    out = []; token = None
    while True:
        path = f"/v2/account/activities?activity_types=FILL&after={after}&page_size=100"
        if token:
            path += f"&page_token={token}"
        acts = api("GET", TRADE_HOST, path, key, sec)
        if not isinstance(acts, list) or not acts:
            break
        out += [a for a in acts if a.get("symbol") == symbol]
        if len(acts) < 100:
            break
        token = acts[-1].get("id")
    return out


def close_position(symbol, key, sec):
    return api("DELETE", TRADE_HOST, f"/v2/positions/{symbol}", key, sec)


def replace_order(order_id, key, sec, **fields):
    """PATCH an existing order (e.g. ratchet a stop_price up)."""
    return api("PATCH", TRADE_HOST, f"/v2/orders/{order_id}", key, sec, body=fields)


def cancel_symbol_orders(symbol, key, sec):
    """Cancel all open orders for a symbol (orphaned bracket legs after a close)."""
    for o in get_open_orders(key, sec):
        if o.get("symbol") == symbol:
            try:
                api("DELETE", TRADE_HOST, f"/v2/orders/{o['id']}", key, sec)
            except AlpacaError:
                pass


def open_stop_order(symbol, key, sec):
    """The resting protective stop (bracket leg) for a symbol, if any."""
    for o in get_open_orders(key, sec):
        if (o.get("symbol") == symbol and o.get("side") == "sell"
                and (o.get("type") or "").startswith("stop")):
            return o
    return None


def close_all(key, sec):
    return api("DELETE", TRADE_HOST, "/v2/positions?cancel_orders=true", key, sec)


def flatten_symbol(symbol, key, sec, attempts=4, settle=1.5):
    """Cancel the symbol's resting bracket legs, THEN close the position — with a
    settle-and-retry so the flatten actually completes within the tick.

    Cancelling a bracket leg does not release its shares synchronously: Alpaca
    keeps them `held_for_orders` for a moment, so a close_position() fired
    immediately after the cancel can still 403 (code 40310000, "insufficient qty
    available"). Cancel-first ordering alone is necessary but NOT sufficient. We
    re-cancel and retry a few times, tolerating only that specific 403, so the
    kill switch can't mark `halted` while leaving the position open, and a
    CLOSE_SIGNAL can't crash the tick on the race. Any other error propagates.
    """
    last_err = None
    for i in range(attempts):
        cancel_symbol_orders(symbol, key, sec)
        try:
            return close_position(symbol, key, sec)
        except AlpacaError as e:
            last_err = e
            msg = str(e)
            # Only the held_for_orders race is retryable; anything else is real.
            if "40310000" not in msg and "insufficient qty" not in msg:
                raise
            if i < attempts - 1:
                time.sleep(settle)
    raise last_err


def submit_bracket(symbol, qty, entry_stop, take_profit, key, sec, side="buy"):
    order = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        # GTC so the protective stop/TP legs survive overnight. With "day" they
        # are canceled at market close, leaving a multi-day position unprotected.
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": round(take_profit, 2)},
        "stop_loss": {"stop_price": round(entry_stop, 2)},
    }
    return api("POST", TRADE_HOST, "/v2/orders", key, sec, body=order)


def submit_stop(symbol, qty, stop_price, key, sec):
    """Plain GTC protective sell-stop. Used only to re-arm a position that has
    lost its bracket stop leg (e.g. legs cancelled by a flatten that then failed)."""
    order = {"symbol": symbol, "qty": str(int(qty)), "side": "sell", "type": "stop",
             "time_in_force": "gtc", "stop_price": str(round(float(stop_price), 2))}
    return api("POST", TRADE_HOST, "/v2/orders", key, sec, body=order)


# ---- indicators -----------------------------------------------------------

def atr(bars: list[dict], period: int = 14) -> float:
    """Latest Wilder ATR, matching backtest.atr_series (seed = mean of the first
    `period` true ranges after bar 0, then Wilder smoothing). Previously a simple
    mean of the last `period` TRs, which diverged from the validated backtest by
    up to ~10% on live bars (PLTR 5.42 vs 6.01 on 2026-10-06) and so mis-sized
    positions and mis-placed stops relative to what was backtested."""
    trs, prev_close = [], None
    for b in bars:
        h, l, c = b["h"], b["l"], b["c"]
        tr = (h - l) if prev_close is None else max(
            h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) <= period:
        raise AlpacaError(f"need > {period} bars for ATR, got {len(trs)}")
    a = sum(trs[1:period + 1]) / period
    for t in trs[period + 1:]:
        a = (a * (period - 1) + t) / period
    return a


def adx(bars: list[dict], period: int = 14) -> float | None:
    """Latest Wilder ADX (trend-strength). None if not enough data."""
    n = len(bars)
    if n <= 2 * period:
        return None
    tr = [0.0] * n; pdm = [0.0] * n; ndm = [0.0] * n
    for i in range(1, n):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        pc = bars[i - 1]["c"]
        tr[i] = max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc),
                    abs(bars[i]["l"] - pc))
    str_ = sum(tr[1:period + 1]); spdm = sum(pdm[1:period + 1]); sndm = sum(ndm[1:period + 1])
    dxs = []
    for i in range(period + 1, n):
        str_ = str_ - str_ / period + tr[i]
        spdm = spdm - spdm / period + pdm[i]
        sndm = sndm - sndm / period + ndm[i]
        pdi = 100 * spdm / str_ if str_ else 0
        ndi = 100 * sndm / str_ if str_ else 0
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0
        dxs.append(dx)
    if len(dxs) < period:
        return None
    a = sum(dxs[:period]) / period
    for j in range(period, len(dxs)):
        a = (a * (period - 1) + dxs[j]) / period
    return a


def rsi(bars: list[dict], period: int = 14) -> float | None:
    """Latest Wilder RSI. None if not enough data."""
    n = len(bars)
    if n <= period:
        return None
    g = [0.0] * n; l = [0.0] * n
    for i in range(1, n):
        ch = bars[i]["c"] - bars[i - 1]["c"]
        g[i] = max(ch, 0.0); l[i] = max(-ch, 0.0)
    ag = sum(g[1:period + 1]) / period; al = sum(l[1:period + 1]) / period
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
    return 100 - 100 / (1 + (ag / al if al else 999))


def sma(bars: list[dict], period: int) -> float | None:
    if period <= 0 or len(bars) < period:
        return None
    return sum(b["c"] for b in bars[-period:]) / period


def ema_pair(bars: list[dict], period: int = 20) -> tuple[float, float]:
    """Return (current EMA, prior-bar EMA) to read the slope."""
    closes = [b["c"] for b in bars]
    if len(closes) < period:
        raise AlpacaError(f"need >= {period} bars for EMA, got {len(closes)}")
    k = 2 / (period + 1)
    e = sum(closes[:period]) / period
    series = [e]
    for c in closes[period:]:
        e = c * k + e * (1 - k)
        series.append(e)
    return series[-1], (series[-2] if len(series) >= 2 else series[-1])
