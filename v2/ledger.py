"""SQLite ledger for the v2 fleet: durable state + an append-only record of every
tick, decision and order. One file (fleet.db) replaces v1's state.json /
journal.jsonl / equity.csv / portfolio.json per bot. Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "fleet.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, val TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticks (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, et_date TEXT NOT NULL, mode TEXT NOT NULL,
  equity REAL, core_mv REAL, size_equity REAL, bots_mv REAL, port_pnl REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, et_date TEXT NOT NULL, symbol TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
  qty REAL NOT NULL, kind TEXT NOT NULL, order_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS equity_daily (
  et_date TEXT PRIMARY KEY, equity REAL, core_mv REAL, bots_mv REAL, cash REAL, updated TEXT);
"""


class Ledger:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.con = sqlite3.connect(path, timeout=30)
        self.con.executescript(SCHEMA)
        self.con.commit()

    # -- durable state ------------------------------------------------------
    def get(self, key: str, default=None):
        row = self.con.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, val) -> None:
        self.con.execute("INSERT OR REPLACE INTO kv(key,val) VALUES(?,?)", (key, json.dumps(val)))
        self.con.commit()

    # -- append-only records -----------------------------------------------
    def tick(self, ts, et_date, mode, equity, core_mv, size_equity, bots_mv, port_pnl, detail) -> None:
        self.con.execute(
            "INSERT INTO ticks(ts,et_date,mode,equity,core_mv,size_equity,bots_mv,port_pnl,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (ts, et_date, mode, equity, core_mv, size_equity, bots_mv, port_pnl, json.dumps(detail, default=str)))
        self.con.execute(
            "INSERT OR REPLACE INTO equity_daily(et_date,equity,core_mv,bots_mv,cash,updated) VALUES(?,?,?,?,?,?)",
            (et_date, equity, core_mv, bots_mv, detail.get("cash"), ts))
        self.con.commit()

    def decision(self, ts, et_date, symbol, action, detail) -> None:
        self.con.execute("INSERT INTO decisions(ts,et_date,symbol,action,detail) VALUES(?,?,?,?,?)",
                         (ts, et_date, symbol, action, json.dumps(detail, default=str)))
        self.con.commit()

    def order(self, ts, symbol, side, qty, kind, order_id, detail) -> None:
        self.con.execute("INSERT INTO orders(ts,symbol,side,qty,kind,order_id,detail) VALUES(?,?,?,?,?,?,?)",
                         (ts, symbol, side, qty, kind, order_id, json.dumps(detail, default=str)))
        self.con.commit()

    # -- reads for status ---------------------------------------------------
    def last_tick(self):
        r = self.con.execute("SELECT ts,et_date,mode,equity,core_mv,size_equity,bots_mv,port_pnl FROM ticks "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(zip(["ts", "et_date", "mode", "equity", "core_mv", "size_equity", "bots_mv", "port_pnl"], r)) if r else None

    def recent_decisions(self, n=15):
        rows = self.con.execute("SELECT ts,symbol,action,detail FROM decisions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(ts=a, symbol=b, action=c, detail=json.loads(d)) for a, b, c, d in rows]

    def equity_curve(self):
        return self.con.execute("SELECT et_date,equity FROM equity_daily ORDER BY et_date").fetchall()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
