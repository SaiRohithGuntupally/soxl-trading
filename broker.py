"""
Thin Alpaca paper-API client + indicators, shared by bot.py and tooling.
Stdlib only. Raises AlpacaError on failure (callers decide how to handle) so a
long-running loop never dies on a transient HTTP blip.
"""

from __future__ import annotations

import datetime as dt
import json
import os
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


def todays_fills(symbol, date, key, sec):
    """All FILL activities for `symbol` on `date` (YYYY-MM-DD)."""
    acts = api("GET", TRADE_HOST,
               f"/v2/account/activities?activity_types=FILL&date={date}", key, sec)
    if not isinstance(acts, list):
        return []
    return [a for a in acts if a.get("symbol") == symbol]


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


# ---- indicators -----------------------------------------------------------

def atr(bars: list[dict], period: int = 14) -> float:
    trs, prev_close = [], None
    for b in bars:
        h, l, c = b["h"], b["l"], b["c"]
        tr = (h - l) if prev_close is None else max(
            h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) < period:
        raise AlpacaError(f"need >= {period} bars for ATR, got {len(trs)}")
    return sum(trs[-period:]) / period


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
