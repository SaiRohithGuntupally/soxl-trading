"""
v2 fleet engine: ONE process, ONE tick for the whole portfolio.

What changed versus v1 (bot.py x7) and why, each backed by research/PROFIT-REVIEW-2026-10-06.md:
  1. Signals are evaluated ONCE per day, in the last `decision_window_min` minutes of
     the session, on the near-complete daily bar. v1 evaluated the partial bar every
     15 minutes and 38% of its trades were same-day round trips the backtest never saw
     (SOXL: -18% live-mode vs +61% end-of-day on a 10-year 30-min replay).
     Every other tick is MANAGE-ONLY: kill switch, portfolio breaker, stop re-arm,
     trailing ratchet. Nothing opens or closes on a signal outside the window.
  2. A passive core (SPY, 50% of equity) holds the capital the bots leave idle 45%
     of the time, and the bots size their risk off equity MINUS the core, which also
     caps gross exposure at 100% by construction (Sharpe 0.73 -> 0.91, maxDD 45% -> 30%).
  3. Only the 5 trend bots. The mean-reversion bots had 8 trades in 10 years.
  4. One SQLite ledger instead of 7 x (state.json, journal.jsonl, equity.csv).
  5. Hard guardrails in code; v1-collision guard; heartbeat built in.

Honest expectation (from the review): forward returns are SPY-like with a lower
drawdown; the trend sleeve has no statistically demonstrated edge. This app is a
cleaner, cheaper, safer implementation of a defensible allocation, not a money printer.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import math
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import broker  # noqa: E402  (v1 Alpaca client + Wilder indicators, audited 2026-10-06)
import ledger as ledger_mod  # noqa: E402

try:
    import notify  # noqa: E402
except Exception:  # pragma: no cover - notify is best-effort everywhere
    notify = None

CONFIG_PATH = os.path.join(HERE, "config.json")

# ---- guardrails: enforced in code no matter what config says -------------------
HARD_KILL_CEILING = 10.0
HARD_PORTFOLIO_CEILING = 15.0
HARD_RISK_CEILING = {"SOXL": 4.0}
HARD_RISK_DEFAULT = 2.0
HARD_GROSS_CEILING = 100.0
V1_COLLISION_MINUTES = 30


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def _notify(msg: str) -> None:
    try:
        if notify is not None:
            notify.send(msg)
    except Exception:
        pass


# ---- config -------------------------------------------------------------------

def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path) as fh:
        raw = json.load(fh)
    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    acct = cfg.setdefault("account", {})
    acct["max_daily_loss_pct"] = min(float(acct.get("max_daily_loss_pct", 10.0)), HARD_KILL_CEILING)
    acct["portfolio_max_loss_pct"] = min(float(acct.get("portfolio_max_loss_pct", 15.0)), HARD_PORTFOLIO_CEILING)
    acct["max_gross_pct"] = min(float(acct.get("max_gross_pct", 100.0)), HARD_GROSS_CEILING)
    acct.setdefault("max_position_pct", 50.0)
    core = cfg.setdefault("core", {"symbol": "SPY", "fraction": 0.0, "rebalance_band": 0.10})
    core["fraction"] = max(0.0, min(float(core.get("fraction", 0.0)), 0.9))
    core.setdefault("rebalance_band", 0.10)
    defaults = {k: v for k, v in cfg.get("defaults", {}).items() if not k.startswith("_")}
    bots = []
    for b in cfg.get("bots", []):
        bb = dict(defaults)
        bb.update({k: v for k, v in b.items() if not k.startswith("_")})
        bb.setdefault("feed", cfg.get("feed", "iex"))
        cap = HARD_RISK_CEILING.get(bb["symbol"], HARD_RISK_DEFAULT)
        bb["risk_pct"] = min(float(bb.get("risk_pct", 2.0)), cap)
        if bb["symbol"] == core["symbol"]:
            raise ValueError(f"bot symbol {bb['symbol']} collides with the core symbol")
        bots.append(bb)
    cfg["bots"] = bots
    cfg.setdefault("decision_window_min", 20)
    return cfg


def bars_lookback_days(b: dict) -> int:
    need = max(int(b.get("ema_len") or 0), int(b.get("regime_ma") or 0),
               int(b.get("mkt_ema") or 20), 14 * 3)
    return max(160, int(need * 1.5) + 30)


# ---- time helpers --------------------------------------------------------------

def _parse_ts(ts: str) -> dt.datetime:
    s = ts.replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        tz = tail[len("".join(ch for ch in tail if ch.isdigit())):]
        s = f"{head}.{digits.ljust(6, '0')}{tz}" if digits else f"{head}{tz}"
    return dt.datetime.fromisoformat(s)


def in_decision_window(clock: dict, window_min: int) -> bool:
    """True inside the last `window_min` minutes of an OPEN session. Uses the API's
    own next_close so early-close days (13:00 ET) work without a calendar."""
    if not clock.get("is_open"):
        return False
    try:
        now = _parse_ts(clock["timestamp"])
        close = _parse_ts(clock["next_close"])
    except (KeyError, ValueError):
        return False
    remaining = (close - now).total_seconds() / 60.0
    return 0 <= remaining <= window_min


# ---- the engine ------------------------------------------------------------------

class Engine:
    def __init__(self, cfg: dict, brk=broker, ledger=None, dry_run: bool = False, force: bool = False):
        self.cfg = cfg
        self.b = brk                    # injectable for tests
        self.L = ledger or ledger_mod.Ledger()
        self.dry = dry_run
        self.force = force
        self.key, self.sec = self.b.load_creds()
        self.symbols = [x["symbol"] for x in cfg["bots"]]

    # -- helpers -------------------------------------------------------------------
    def _pos(self, positions, sym):
        for p in positions:
            if p.get("symbol") == sym:
                return p
        return None

    def _bot_state(self, sym) -> dict:
        return self.L.get(f"bot:{sym}", {}) or {}

    def _save_bot(self, sym, st) -> None:
        self.L.set(f"bot:{sym}", st)

    def _v1_alive(self) -> list:
        """v1 run scripts stamp ROOT/heartbeat/<bot>. If any is fresh, v1 crons are
        still ticking on this account and v2 must not trade the same symbols."""
        fresh = []
        for p in glob.glob(os.path.join(ROOT, "heartbeat", "*")):
            if os.path.basename(p) in ("operator",):
                continue
            try:
                age = (time.time() - os.path.getmtime(p)) / 60.0
                if age < V1_COLLISION_MINUTES:
                    fresh.append(os.path.basename(p))
            except OSError:
                pass
        return fresh

    def _heartbeat(self, rc: int) -> None:
        try:
            d = os.path.join(HERE, "heartbeat")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "fleet"), "w") as fh:
                fh.write(f"{ledger_mod.now_iso()} rc={rc}\n")
            url = None
            envp = os.path.join(ROOT, ".env")
            if os.path.exists(envp):
                for line in open(envp):
                    if line.startswith("HEALTHCHECK_URL="):
                        url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if url and not self.dry:
                if rc != 0:
                    url = url.rstrip("/") + "/fail"
                urllib.request.urlopen(url, timeout=10).read()
        except Exception:
            pass

    # -- P&L: cashflow method per symbol (position value delta + today's fills) -------
    def _symbol_pnl(self, sym, today, mv_now, mv_start) -> float:
        try:
            fills = self.b.todays_fills(sym, today, self.key, self.sec)
        except Exception:
            fills = []
        cash = 0.0
        for f in fills:
            q = float(f.get("qty") or 0) * float(f.get("price") or 0)
            cash += q if (f.get("side") or "").startswith("sell") else -q
        return (mv_now - mv_start) + cash

    # -- signal -----------------------------------------------------------------------
    def gate(self, bot: dict, today: str) -> dict:
        und = bot["underlying"]
        bars = self.b.daily_bars(und, self.key, self.sec, lookback_days=bars_lookback_days(bot), feed=bot["feed"])
        close = bars[-1]["c"]
        ema, ema_prev = self.b.ema_pair(bars, int(bot["ema_len"]))
        adx = self.b.adx(bars, 14) if bot.get("chop_filter") else None
        chop_ok = (not bot.get("chop_filter")) or (adx is not None and adx >= float(bot["adx_min"]))
        mkt_ok = True
        if bot.get("mkt_confirm"):
            try:
                sb = self.b.daily_bars(bot.get("mkt_symbol", "SPY"), self.key, self.sec, feed=bot["feed"])
                se, _ = self.b.ema_pair(sb, int(bot.get("mkt_ema", 20)))
                mkt_ok = sb[-1]["c"] > se
            except Exception:
                mkt_ok = True           # fail open on a SPY data hiccup, like v1
        blk = int(bot.get("event_block_days") or 0)
        event_block = False
        if blk:
            t = dt.date.fromisoformat(today)
            for d in bot.get("event_dates") or []:
                try:
                    if 0 <= (dt.date.fromisoformat(d) - t).days <= blk:
                        event_block = True
                except ValueError:
                    pass
        above = close > ema
        rising = ema > ema_prev
        return {"close": round(close, 2), "ema": round(ema, 2), "ema_rising": rising, "above_ema": above,
                "adx": None if adx is None else round(adx, 1), "chop_ok": chop_ok, "mkt_ok": mkt_ok,
                "event_block": event_block,
                "enter": bool(above and rising and chop_ok and mkt_ok and not event_block),
                "exit": bool(not above), "bars": len(bars)}

    # -- sizing off NON-CORE capital, capped by position and gross limits ---------------
    def size(self, bot: dict, price: float, atr: float, size_equity: float, bots_mv: float) -> dict:
        acct = self.cfg["account"]
        stop_dist = float(bot["stop_atr"]) * atr
        if price <= 0 or stop_dist <= 0 or size_equity <= 0:
            return {"shares": 0, "reason": "bad inputs"}
        risk_dollars = size_equity * float(bot["risk_pct"]) / 100.0
        by_risk = math.floor(risk_dollars / stop_dist)
        by_pos = math.floor(size_equity * float(acct["max_position_pct"]) / 100.0 / price)
        gross_room = size_equity * float(acct["max_gross_pct"]) / 100.0 - bots_mv
        by_gross = max(0, math.floor(gross_room / price))
        shares = max(0, min(by_risk, by_pos, by_gross))
        return {"shares": shares, "stop_price": round(price - stop_dist, 2), "risk_dollars": round(risk_dollars, 2),
                "by_risk": by_risk, "by_pos": by_pos, "by_gross": by_gross, "atr": round(atr, 4)}

    # -- protective stop management (every tick) ------------------------------------------
    def manage_stop(self, bot: dict, pos: dict, orders: list, st: dict, rec: dict) -> bool:
        """Re-arm a missing stop; ratchet the chandelier up (never down); flatten if the
        chandelier is already crossed. Returns True if the position was flattened."""
        sym = bot["symbol"]
        try:
            qty = int(float(pos.get("qty") or 0))
            if qty <= 0:
                return False
            price = float(pos.get("current_price") or pos["avg_entry_price"])
            entry = float(pos["avg_entry_price"])
            atr_now = self.b.atr(self.b.daily_bars(sym, self.key, self.sec, feed=bot["feed"]), int(bot["atr_len"]))
            hh = max(float(st.get("trail_hh") or entry), price)
            st["trail_hh"] = hh
            level = float(st.get("stop_price") or (entry - float(bot["stop_atr"]) * atr_now))
            if bot.get("trailing"):
                level = max(level, hh - float(bot["chand_atr"]) * atr_now)
            level = round(level, 2)
            rec["stop_level"] = level
            resting = [o for o in orders if o.get("symbol") == sym]
            stop_o = next((o for o in resting if o.get("side") == "sell" and (o.get("type") or "").startswith("stop")), None)
            if level >= price:
                if not self.dry:
                    self.b.flatten_symbol(sym, self.key, self.sec)
                rec["action"] = "CLOSE_TRAIL"
                rec["note"] = f"stop level {level:.2f} >= price {price:.2f}"
                _notify(f"🔻 v2 {sym} trailing level ${level:.2f} crossed (price ${price:.2f}) -> flattened")
                return True
            if stop_o is None:
                if resting:
                    rec["note"] = "orders resting but no stop leg; leaving alone"
                    return False
                if not self.dry:
                    r = self.b.submit_stop(sym, qty, level, self.key, self.sec)
                    self.L.order(ledger_mod.now_iso(), sym, "sell", qty, "rearm_stop", r.get("id"), {"stop": level})
                st["stop_price"] = level
                rec["rearm_stop"] = level
                _notify(f"⚠️ v2 {sym} had no resting stop; re-armed GTC stop {qty} @ ${level:.2f}")
                return False
            cur = float(stop_o.get("stop_price") or 0)
            if level > cur + 0.01:
                if not self.dry:
                    self.b.replace_order(stop_o["id"], self.key, self.sec, stop_price=level)
                st["stop_price"] = level
                rec["trail_moved_to"] = level
        except Exception as e:  # noqa: BLE001 - stop management must never crash the tick
            rec["stop_error"] = str(e)[:160]
        return False

    # -- core sleeve (decision tick only) -----------------------------------------------------
    def manage_core(self, equity: float, positions: list, rec: dict) -> None:
        core = self.cfg["core"]
        sym = core["symbol"]
        frac = float(core["fraction"])
        if frac <= 0:
            return
        pos = self._pos(positions, sym)
        mv = float(pos["market_value"]) if pos else 0.0
        px = float(pos["current_price"]) if pos and pos.get("current_price") else None
        if px is None:
            try:
                px = self.b.latest_price(sym, self.key, self.sec, feed=self.cfg.get("feed", "iex"))
            except Exception as e:
                rec["core_error"] = f"no price: {e}"[:120]
                return
        target = equity * frac
        band = float(core["rebalance_band"]) * equity
        rec["core"] = {"mv": round(mv, 2), "target": round(target, 2), "pct": round(mv / equity * 100, 1) if equity else 0}
        if abs(mv - target) <= band and mv > 0:
            return
        diff_sh = int((target - mv) / px)
        if diff_sh == 0:
            return
        side = "buy" if diff_sh > 0 else "sell"
        qty = abs(diff_sh)
        if side == "sell" and pos:
            qty = min(qty, int(float(pos.get("qty_available") or pos.get("qty") or 0)))
        if qty <= 0:
            return
        rec["core"]["order"] = f"{side} {qty}"
        if self.dry:
            return
        try:
            r = self.b.submit_market(sym, qty, side, self.key, self.sec)
            self.L.order(ledger_mod.now_iso(), sym, side, qty, "core_rebalance", r.get("id"), rec["core"])
            _notify(f"🏛️ v2 core {sym}: {side} {qty} (core {rec['core']['pct']}% -> target {frac*100:.0f}%)")
        except Exception as e:  # noqa: BLE001
            rec["core_error"] = str(e)[:160]

    # -- one tick ------------------------------------------------------------------------------
    def tick(self, force_decide: bool = False) -> dict:
        cfg = self.cfg
        acct_cfg = cfg["account"]
        rec = {"ts": ledger_mod.now_iso(), "dry_run": self.dry, "bots": {}}
        alive = self._v1_alive()
        if alive and not self.dry and not self.force:
            rec["mode"] = "REFUSED"
            rec["note"] = f"v1 bots still ticking ({', '.join(alive)}); run ./install_cron.sh to retire v1 or pass --force"
            log(rec["note"])
            self.L.tick(rec["ts"], "n/a", "REFUSED", None, None, None, None, None, rec)
            return rec

        acct = self.b.get_account(self.key, self.sec)
        clock = self.b.get_clock(self.key, self.sec)
        equity = float(acct["equity"])
        today = clock["timestamp"][:10]
        positions = self.b.get_positions(self.key, self.sec)
        orders = self.b.get_open_orders(self.key, self.sec)
        core_sym = cfg["core"]["symbol"]
        core_pos = self._pos(positions, core_sym)
        core_mv = float(core_pos["market_value"]) if core_pos and float(cfg["core"]["fraction"]) > 0 else 0.0
        size_equity = max(0.0, equity - core_mv)
        bot_pos = {s: self._pos(positions, s) for s in self.symbols}
        bots_mv = sum(float(p["market_value"]) for p in bot_pos.values() if p)

        # day rollover (ET date from the API clock)
        day = self.L.get("day", {}) or {}
        if day.get("date") != today:
            day = {"date": today, "equity": equity, "size_equity": size_equity, "decided": False,
                   "halted_all": False, "halt_reason": None,
                   "start_mv": {s: (float(p["market_value"]) if p else 0.0) for s, p in bot_pos.items()}}
            for s in self.symbols:
                st = self._bot_state(s)
                st["halted"] = False
                if not bot_pos[s]:
                    st["trail_hh"] = None
                    st["stop_price"] = None
                self._save_bot(s, st)
            self.L.set("day", day)

        decide = force_decide or (in_decision_window(clock, int(cfg["decision_window_min"])) and not day.get("decided"))
        rec["mode"] = "DECIDE" if decide else ("MANAGE" if clock.get("is_open") else "CLOSED")
        rec.update({"et_date": today, "equity": round(equity, 2), "core_mv": round(core_mv, 2),
                    "size_equity": round(size_equity, 2), "bots_mv": round(bots_mv, 2),
                    "gross_pct": round(bots_mv / size_equity * 100, 1) if size_equity else 0.0,
                    "cash": float(acct.get("cash") or 0)})

        # ---- per-bot P&L + kill switches (every tick, even when closed: flatten still works) ----
        base = float(day.get("size_equity") or size_equity) or equity
        kill_level = -float(acct_cfg["max_daily_loss_pct"]) / 100.0 * base
        port_pnl = 0.0
        pnls = {}
        for s in self.symbols:
            p = bot_pos[s]
            mv = float(p["market_value"]) if p else 0.0
            pnl = self._symbol_pnl(s, today, mv, float(day["start_mv"].get(s, 0.0)))
            pnls[s] = pnl
            port_pnl += pnl
        rec["port_pnl"] = round(port_pnl, 2)
        port_breach = port_pnl <= -float(acct_cfg["portfolio_max_loss_pct"]) / 100.0 * base
        if port_breach and not day.get("halted_all"):
            day["halted_all"] = True
            day["halt_reason"] = f"portfolio breaker: bots P&L ${port_pnl:.0f} <= -{acct_cfg['portfolio_max_loss_pct']}% of ${base:.0f}"
            self.L.set("day", day)
            _notify(f"🚨 v2 {day['halt_reason']} -> flattening all bots, halted for the day")
            log(day["halt_reason"])

        for bot in cfg["bots"]:
            s = bot["symbol"]
            st = self._bot_state(s)
            p = bot_pos[s]
            br = {"pnl": round(pnls[s], 2), "has_position": bool(p)}
            if p:
                br["qty"] = p.get("qty"); br["mv"] = round(float(p["market_value"]), 2)
            # kill switch: own-symbol loss or portfolio breach
            if (pnls[s] <= kill_level or day.get("halted_all")) and not st.get("halted"):
                st["halted"] = True
                st["halt_reason"] = day.get("halt_reason") or f"{s} daily P&L ${pnls[s]:.0f} <= -{acct_cfg['max_daily_loss_pct']}%"
                br["action"] = "KILL_SWITCH"; br["note"] = st["halt_reason"]
                if p and not self.dry:
                    try:
                        self.b.flatten_symbol(s, self.key, self.sec)
                        self.L.order(rec["ts"], s, "sell", p.get("qty"), "kill_flatten", None, {"reason": st["halt_reason"]})
                    except Exception as e:  # noqa: BLE001
                        br["flatten_error"] = str(e)[:160]
                self.L.decision(rec["ts"], today, s, "KILL_SWITCH", br)
                _notify(f"🚨 v2 {s} KILL SWITCH: {st['halt_reason']}")
                self._save_bot(s, st); rec["bots"][s] = br
                continue
            if st.get("halted"):
                br["action"] = "HALTED"; br["note"] = st.get("halt_reason")
                if p and not self.dry:        # retry a flatten that failed earlier
                    try:
                        self.b.flatten_symbol(s, self.key, self.sec)
                    except Exception as e:  # noqa: BLE001
                        br["flatten_error"] = str(e)[:160]
                self._save_bot(s, st); rec["bots"][s] = br
                continue
            # manage the protective stop every tick while holding
            if p and clock.get("is_open"):
                if self.manage_stop(bot, p, orders, st, br):
                    self.L.decision(rec["ts"], today, s, "CLOSE_TRAIL", br)
                    st["trail_hh"] = None; st["stop_price"] = None
                    self._save_bot(s, st); rec["bots"][s] = br
                    continue
            # ---- decision tick: evaluate the signal on the near-complete bar ----
            if decide:
                try:
                    g = self.gate(bot, today)
                except Exception as e:  # noqa: BLE001
                    br["action"] = "GATE_ERROR"; br["note"] = str(e)[:160]
                    self._save_bot(s, st); rec["bots"][s] = br
                    continue
                br["gate"] = g
                if p and g["exit"]:
                    br["action"] = "CLOSE_SIGNAL"; br["note"] = f"{bot['underlying']} {g['close']} below EMA{bot['ema_len']} {g['ema']}"
                    if not self.dry:
                        try:
                            self.b.flatten_symbol(s, self.key, self.sec)
                            self.L.order(rec["ts"], s, "sell", p.get("qty"), "close_signal", None, br["note"])
                            st["trail_hh"] = None; st["stop_price"] = None
                        except Exception as e:  # noqa: BLE001
                            br["flatten_error"] = str(e)[:160]
                    _notify(f"🔴 v2 {s} CLOSE: {br['note']}")
                elif p:
                    br["action"] = "HOLD"
                elif g["enter"] and not any(o.get("symbol") == s for o in orders):
                    try:
                        own = self.b.daily_bars(s, self.key, self.sec, feed=bot["feed"])
                        price = float(own[-1]["c"])
                        atr_now = self.b.atr(own, int(bot["atr_len"]))
                        plan = self.size(bot, price, atr_now, size_equity, bots_mv)
                        br["plan"] = plan
                        if plan["shares"] <= 0:
                            br["action"] = "NO_SIZE"
                        else:
                            tp_mult = float(bot["trail_tp_R"]) if bot.get("trailing") else float(bot["tp_R"])
                            tp = round(price + (price - plan["stop_price"]) * tp_mult, 2)
                            br["action"] = "DRY_OPEN" if self.dry else "OPEN"
                            br["note"] = f"buy {plan['shares']} @ ~{price:.2f} stop {plan['stop_price']} tp {tp}"
                            if not self.dry:
                                r = self.b.submit_bracket(s, plan["shares"], plan["stop_price"], tp, self.key, self.sec)
                                self.L.order(rec["ts"], s, "buy", plan["shares"], "bracket_entry", r.get("id"), plan)
                                st["trail_hh"] = price; st["stop_price"] = plan["stop_price"]
                                bots_mv += plan["shares"] * price      # so the next bot sees the gross used
                            _notify(f"🟢 v2 {s} OPEN: {br['note']} (risk {bot['risk_pct']}% of non-core ${size_equity:,.0f})")
                    except Exception as e:  # noqa: BLE001
                        br["action"] = "ENTRY_ERROR"; br["note"] = str(e)[:160]
                else:
                    br["action"] = "FLAT_NO_SIGNAL" if not g["enter"] else "FLAT_ORDER_PENDING"
                self.L.decision(rec["ts"], today, s, br.get("action", "?"), br)
            else:
                br["action"] = "HOLD" if p else ("FLAT" if clock.get("is_open") else "CLOSED")
            self._save_bot(s, st)
            rec["bots"][s] = br

        if decide:
            self.manage_core(equity, positions, rec)
            day["decided"] = True
            self.L.set("day", day)

        self.L.tick(rec["ts"], today, rec["mode"], equity, core_mv, size_equity, bots_mv, port_pnl, rec)
        acts = {s: b.get("action") for s, b in rec["bots"].items()}
        log(f"{rec['mode']} eq ${equity:,.0f} core ${core_mv:,.0f} non-core ${size_equity:,.0f} "
            f"bots ${bots_mv:,.0f} ({rec['gross_pct']}%) P&L ${port_pnl:,.0f} | {acts}")
        return rec

    # -- manual ------------------------------------------------------------------------------------
    def flatten(self, symbols=None) -> None:
        """Close bot positions (never the core, never anything else) and halt them today."""
        for s in symbols or self.symbols:
            if s not in self.symbols:
                log(f"refusing to flatten {s}: not a fleet bot symbol")
                continue
            try:
                self.b.flatten_symbol(s, self.key, self.sec)
                st = self._bot_state(s); st.update({"halted": True, "halt_reason": "manual flatten", "trail_hh": None, "stop_price": None})
                self._save_bot(s, st)
                self.L.order(ledger_mod.now_iso(), s, "sell", 0, "manual_flatten", None, {})
                log(f"flattened {s}")
            except Exception as e:  # noqa: BLE001
                log(f"flatten {s} failed: {e}")
