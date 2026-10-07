"""Synthetic, no-network tests for the v2 engine. Run: python3 -m unittest discover v2/tests"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.dirname(HERE)
sys.path.insert(0, V2)
import engine  # noqa: E402
import ledger as ledger_mod  # noqa: E402


def trend_bars(n=260, start=100.0, step=0.5, wiggle=0.3):
    """Steadily rising bars: close above a rising EMA, decent ADX."""
    bars = []
    px = start
    for i in range(n):
        o = px; px += step; c = px
        bars.append({"t": f"2026-01-{i:02d}", "o": o, "h": max(o, c) + wiggle, "l": min(o, c) - wiggle, "c": c, "v": 1000})
    return bars


class FakeBroker:
    """Same surface as broker.py, scripted answers, records every order."""
    AlpacaError = Exception

    def __init__(self, equity=100000.0, positions=None, orders=None, is_open=True, ts="2026-10-06T15:45:00-04:00",
                 next_close="2026-10-06T16:00:00-04:00", bars=None, fills=None):
        self.equity = equity; self.positions = positions or []; self.orders = orders or []
        self.is_open = is_open; self.ts = ts; self.next_close = next_close
        self.bars = bars or {}; self.fills = fills or {}
        self.placed = []; self.flattened = []; self.replaced = []; self.stops = []

    def load_creds(self): return ("k", "s")
    def get_account(self, k, s): return {"equity": str(self.equity), "cash": "0", "buying_power": str(2 * self.equity)}
    def get_clock(self, k, s): return {"timestamp": self.ts, "is_open": self.is_open, "next_close": self.next_close}
    def get_positions(self, k, s): return self.positions
    def get_open_orders(self, k, s): return self.orders
    def daily_bars(self, sym, k, s, lookback_days=160, feed="iex"): return self.bars.get(sym) or trend_bars()
    def latest_price(self, sym, k, s, feed="iex"): return self.daily_bars(sym, k, s)[-1]["c"]
    def todays_fills(self, sym, date, k, s): return self.fills.get(sym, [])
    def atr(self, bars, period=14): return 2.0
    def adx(self, bars, period=14): return 30.0
    def ema_pair(self, bars, period=20):
        c = [b["c"] for b in bars]; k = 2 / (period + 1); e = c[0]; prev = e
        for x in c[1:]: prev = e; e = x * k + e * (1 - k)
        return e, prev
    def rsi(self, bars, period=14): return 50.0
    def sma(self, bars, period): return sum(b["c"] for b in bars[-period:]) / period if len(bars) >= period else None
    def submit_bracket(self, sym, qty, stop, tp, k, s, side="buy"):
        self.placed.append(("bracket", sym, qty, stop, tp)); return {"id": f"ord-{sym}"}
    def submit_market(self, sym, qty, side, k, s, tif="day"):
        self.placed.append(("market", sym, qty, side)); return {"id": f"mkt-{sym}"}
    def submit_stop(self, sym, qty, stop, k, s):
        self.stops.append((sym, qty, stop)); return {"id": f"stop-{sym}"}
    def replace_order(self, oid, k, s, **f): self.replaced.append((oid, f)); return {"id": oid}
    def flatten_symbol(self, sym, k, s, attempts=4, settle=1.5): self.flattened.append(sym); return {}


def pos(sym, qty, price, entry=None):
    return {"symbol": sym, "qty": str(qty), "qty_available": str(qty), "market_value": str(qty * price),
            "current_price": str(price), "avg_entry_price": str(entry or price), "unrealized_pl": "0"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = engine.load_config(os.path.join(V2, "config.json"))
        # keep the fleet small + deterministic for tests
        self.cfg["bots"] = [b for b in self.cfg["bots"] if b["symbol"] in ("SOXL", "UPRO")]
        for b in self.cfg["bots"]:
            b["mkt_confirm"] = False
        engine.ROOT = self.tmp                     # no v1 heartbeats here

    def make(self, fb, **kw):
        L = ledger_mod.Ledger(os.path.join(self.tmp, "t.db"))
        return engine.Engine(self.cfg, brk=fb, ledger=L, **kw)


class TestWindow(unittest.TestCase):
    def test_window_last_20_min(self):
        clk = {"is_open": True, "timestamp": "2026-10-06T15:45:00-04:00", "next_close": "2026-10-06T16:00:00-04:00"}
        self.assertTrue(engine.in_decision_window(clk, 20))
        clk["timestamp"] = "2026-10-06T15:30:00-04:00"
        self.assertFalse(engine.in_decision_window(clk, 20))
        clk["timestamp"] = "2026-10-06T10:00:00-04:00"
        self.assertFalse(engine.in_decision_window(clk, 20))

    def test_early_close_day(self):
        clk = {"is_open": True, "timestamp": "2026-11-27T12:45:00-05:00", "next_close": "2026-11-27T13:00:00-05:00"}
        self.assertTrue(engine.in_decision_window(clk, 20))

    def test_closed_market_never_decides(self):
        clk = {"is_open": False, "timestamp": "2026-10-06T15:45:00-04:00", "next_close": "2026-10-07T16:00:00-04:00"}
        self.assertFalse(engine.in_decision_window(clk, 20))


class TestGuardrails(unittest.TestCase):
    def test_caps_enforced(self):
        cfg = engine.load_config(os.path.join(V2, "config.json"))
        self.assertLessEqual(cfg["account"]["max_daily_loss_pct"], 10.0)
        self.assertLessEqual(cfg["account"]["portfolio_max_loss_pct"], 15.0)
        self.assertLessEqual(cfg["account"]["max_gross_pct"], 100.0)
        for b in cfg["bots"]:
            self.assertLessEqual(b["risk_pct"], 4.0 if b["symbol"] == "SOXL" else 2.0)


class TestSizing(Base):
    def test_sizes_off_non_core_and_caps_gross(self):
        E = self.make(FakeBroker(), dry_run=True)
        bot = self.cfg["bots"][0]  # SOXL risk 4%, stop 1.5 ATR
        plan = E.size(bot, price=100.0, atr=2.0, size_equity=50000.0, bots_mv=0.0)
        self.assertEqual(plan["by_risk"], int(50000 * 0.04 / 3.0))    # 666 by risk...
        self.assertEqual(plan["shares"], 250)                           # ...but the 50% position cap binds ($25k / $100)
        self.assertEqual(plan["stop_price"], 97.0)
        # gross room: only $10k left of non-core -> 100 shares max
        plan2 = E.size(bot, price=100.0, atr=2.0, size_equity=50000.0, bots_mv=40000.0)
        self.assertEqual(plan2["shares"], 100)
        # max_position_pct 50% of non-core
        plan3 = E.size(bot, price=10.0, atr=0.01, size_equity=50000.0, bots_mv=0.0)
        self.assertEqual(plan3["shares"], int(50000 * 0.5 / 10))


class TestTick(Base):
    def test_manage_only_outside_window_places_nothing(self):
        fb = FakeBroker(ts="2026-10-06T10:00:00-04:00")
        E = self.make(fb)
        rec = E.tick()
        self.assertEqual(rec["mode"], "MANAGE")
        self.assertEqual(fb.placed, [])
        self.assertTrue(all(b["action"] == "FLAT" for b in rec["bots"].values()))

    def test_decision_tick_enters_and_buys_core(self):
        fb = FakeBroker()
        E = self.make(fb)
        rec = E.tick()
        self.assertEqual(rec["mode"], "DECIDE")
        kinds = [p[0] for p in fb.placed]
        self.assertIn("bracket", kinds)                       # bot entries
        self.assertIn("market", kinds)                        # core buy (no SPY held)
        core = [p for p in fb.placed if p[0] == "market"][0]
        self.assertEqual(core[1], self.cfg["core"]["symbol"]); self.assertEqual(core[3], "buy")
        # day one: the core is not held yet but its target capital is RESERVED, so the
        # bots size off equity * (1 - fraction); bots + core never exceed 100% gross
        self.assertEqual(rec["bots"]["SOXL"]["action"], "OPEN")
        self.assertEqual(rec["size_equity"], 100000.0 * (1 - self.cfg["core"]["fraction"]))
        bot_notional = sum(p[2] * 100.0 for p in fb.placed if p[0] == "bracket")   # fake bars close ~ $230
        self.assertLessEqual(bot_notional, 100000.0 * (1 - self.cfg["core"]["fraction"]) + 1)
        # second tick same day: already decided -> no new decision
        rec2 = E.tick()
        self.assertEqual(rec2["mode"], "MANAGE")

    def test_decision_tick_sizes_off_equity_minus_core(self):
        fb = FakeBroker(equity=100000.0, positions=[pos(self.cfg["core"]["symbol"], 100, 500.0)])  # core = $50k
        E = self.make(fb)
        rec = E.tick()
        self.assertEqual(rec["size_equity"], 50000.0)
        plan = rec["bots"]["SOXL"]["plan"]
        self.assertEqual(plan["risk_dollars"], 2000.0)        # 4% of $50k, not of $100k
        self.assertFalse(any(p[0] == "market" for p in fb.placed))  # core within band: no rebalance

    def test_core_rebalance_only_outside_band(self):
        fb = FakeBroker(equity=100000.0, positions=[pos(self.cfg["core"]["symbol"], 50, 500.0)])  # core 25% -> below 40%
        E = self.make(fb)
        E.tick()
        core = [p for p in fb.placed if p[0] == "market"]
        self.assertEqual(len(core), 1); self.assertEqual(core[0][3], "buy"); self.assertEqual(core[0][2], 50)

    def test_exit_signal_closes_at_decision_tick(self):
        falling = trend_bars(step=-0.5, start=300.0)
        # entry == price so the chandelier (150 - 6 = 144) is NOT crossed; the EMA break must do the exit
        fb = FakeBroker(positions=[pos("SOXL", 10, 150.0, entry=150.0)], bars={"SOXX": falling},
                        orders=[{"symbol": "SOXL", "side": "sell", "type": "stop", "stop_price": "140", "id": "s1"}])
        E = self.make(fb)
        rec = E.tick()
        self.assertEqual(rec["bots"]["SOXL"]["action"], "CLOSE_SIGNAL")
        self.assertIn("SOXL", fb.flattened)

    def test_exit_signal_ignored_outside_window(self):
        falling = trend_bars(step=-0.5, start=300.0)
        fb = FakeBroker(ts="2026-10-06T11:00:00-04:00", positions=[pos("SOXL", 10, 150.0, entry=140.0)],
                        bars={"SOXX": falling},
                        orders=[{"symbol": "SOXL", "side": "sell", "type": "stop", "stop_price": "100", "id": "s1"}])
        E = self.make(fb)
        rec = E.tick()
        self.assertEqual(rec["bots"]["SOXL"]["action"], "HOLD")
        self.assertEqual(fb.flattened, [])

    def test_trailing_ratchets_up_never_down(self):
        fb = FakeBroker(ts="2026-10-06T11:00:00-04:00", positions=[pos("SOXL", 10, 200.0, entry=150.0)],
                        orders=[{"symbol": "SOXL", "side": "sell", "type": "stop", "stop_price": "180", "id": "s1"}])
        E = self.make(fb)
        E.tick()                                          # chandelier = 200 - 3*2 = 194 > 180 -> move up
        self.assertEqual(fb.replaced[-1][1]["stop_price"], 194.0)
        fb.positions = [pos("SOXL", 10, 190.0, entry=150.0)]
        fb.orders[0]["stop_price"] = "194"
        n = len(fb.replaced)
        E.tick()                                          # price fell, chandelier 194 (hh kept) -> no move
        self.assertEqual(len(fb.replaced), n)

    def test_naked_position_gets_stop_rearmed(self):
        fb = FakeBroker(ts="2026-10-06T11:00:00-04:00", positions=[pos("SOXL", 10, 200.0, entry=198.0)], orders=[])
        E = self.make(fb)
        E.tick()
        self.assertEqual(len(fb.stops), 1)
        self.assertEqual(fb.stops[0][0], "SOXL")
        self.assertLess(fb.stops[0][2], 200.0)

    def test_kill_switch_own_symbol(self):
        # day start value $20k, now $5k, no fills -> pnl -15k <= -10% of size_equity (~95k)
        fb = FakeBroker(ts="2026-10-06T11:00:00-04:00", positions=[pos("SOXL", 100, 200.0)])
        E = self.make(fb)
        E.tick()                                          # establishes day start
        fb.positions = [pos("SOXL", 100, 50.0)]
        rec = E.tick()
        self.assertEqual(rec["bots"]["SOXL"]["action"], "KILL_SWITCH")
        self.assertIn("SOXL", fb.flattened)
        rec3 = E.tick()
        self.assertEqual(rec3["bots"]["SOXL"]["action"], "HALTED")   # trips once, then quiet

    def test_portfolio_breaker_halts_all(self):
        fb = FakeBroker(ts="2026-10-06T11:00:00-04:00", equity=100000.0,
                        positions=[pos("SOXL", 100, 100.0), pos("UPRO", 100, 100.0)])
        E = self.make(fb)
        E.tick()
        fb.positions = [pos("SOXL", 100, 20.0), pos("UPRO", 100, 20.0)]   # -16k combined > 15%
        rec = E.tick()
        self.assertTrue(all(b["action"] == "KILL_SWITCH" for b in rec["bots"].values()))
        self.assertEqual(sorted(fb.flattened), ["SOXL", "UPRO"])

    def test_refuses_when_v1_alive(self):
        os.makedirs(os.path.join(self.tmp, "heartbeat"), exist_ok=True)
        open(os.path.join(self.tmp, "heartbeat", "SOXL"), "w").write("x")
        fb = FakeBroker()
        E = self.make(fb)
        rec = E.tick()
        self.assertEqual(rec["mode"], "REFUSED"); self.assertEqual(fb.placed, [])
        rec2 = self.make(fb, force=True).tick()
        self.assertNotEqual(rec2["mode"], "REFUSED")

    def test_flatten_never_touches_core_or_foreign(self):
        core = self.cfg["core"]["symbol"]
        fb = FakeBroker(positions=[pos(core, 100, 500.0), pos("SOXL", 10, 100.0), pos("NVDA", 5, 200.0)])
        E = self.make(fb)
        E.flatten([core, "NVDA", "SOXL"])
        self.assertEqual(fb.flattened, ["SOXL"])


if __name__ == "__main__":
    unittest.main()
