# Operator NOTES

Dated log of autonomous operator changes: what was seen, what changed, why.

## 2026-07-06 — SOXL/all bots: settle-and-retry the flatten (cancel-first alone wasn't enough)

**Bot:** root SOXL (bug is in shared `bot.py`/`broker.py`, so it affects every bot: SOXL, UPRO, LABU, MSTR, PLTR, TNA, TQQQ).

**Evidence (structural safety, not a drawdown tune):**
- `cron.log` line 1613 (TODAY, 07-06, AFTER the 07-03 cancel-first fix): `tick failed: HTTP 403 DELETE /v2/positions/SOXL ... existing_qty:34, held_for_orders:34, available:0`, immediately followed by the next tick's `exit signal while holding -> close SOXL` (the journaled `CLOSE_SIGNAL` at 09:45:03 that succeeded). So one tick 403'd and crashed uncaught, and the exit only fired 15 min later.
- Root cause the 07-03 fix missed: cancelling a bracket leg does NOT release its shares synchronously — Alpaca keeps them `held_for_orders` briefly, so a `close_position()` fired immediately after `cancel_symbol_orders()` can still 403. Cancel-first ordering is necessary but NOT sufficient.
- Two impacts: (1) CLOSE_SIGNAL path had no try/except → the 403 crashes the tick (today's failure; self-heals next tick). (2) KILL-SWITCH path catches the 403 into `flatten_error` but still sets `halted=True`; the `halted` short-circuit then blocks any retry — so a 403 during a REAL loss breach would mark halted while leaving the position OPEN. This is exactly the "kill switch silently fails to flatten" risk the 07-03 note flagged; it was still live.

**Change:** added `broker.flatten_symbol()` — cancel the symbol's resting legs, then close, retrying only the specific held_for_orders 403 (code 40310000) up to 4× with a 1.5s settle; any other error propagates. Routed all three flatten sites through it (kill-switch, CLOSE_SIGNAL, manual `--flatten`) and wrapped the previously-unguarded CLOSE_SIGNAL close in try/except so a persistent failure journals `flatten_error` instead of crashing the tick. Symbol-scoped only — never touches other account positions.

**Validation:** not a strategy change — `python3 backtest.py` unchanged (Long-only risk 4% still 162%/Sharpe 0.89, CURRENT LIVE Sharpe 0.89); `bot.py`/`broker.py` compile+import clean; `flatten_symbol` present.

**Other bots this run:** DO NOTHING. LABU/TQQQ (meanrev) correctly sat out — XBI RSI 77 / QQQ RSI 52, never near the <30 buy trigger. MSTR (price below a falling EMA), PLTR (ADX 18.9<25), TNA (ADX 7.9) all correctly gate-blocked — real data flowing, gates computing, "zero entries" is correct sit-out not a bug. UPRO holding a profitable position, no structural flag. SOXL's rough early-July days are a drawdown, not a bug — not tuned.

## 2026-07-03 — SOXL/all bots: flatten paths now cancel bracket legs before closing

**Bot:** root SOXL (bug is in shared `bot.py`, so it affects every bot: SOXL, UPRO, LABU, MSTR, PLTR, TNA, TQQQ).

**Evidence (structural, not a drawdown tune):**
- `cron.log`: 27× `HTTP 403 DELETE /v2/positions/SOXL: insufficient qty available ... existing_qty:34, held_for_orders:34, available:0`.
- Broker live check: SOXL (34 sh) and UPRO (262 sh) both have `qty_available=0` with a resting **sell limit** (bracket take-profit leg) holding every share.
- `journal.jsonl`: **0** `CLOSE_SIGNAL`, **0** `KILL_SWITCH`, **0** `flatten_error` despite the 27 broker failures — proof the CLOSE_SIGNAL path crashed *uncaught* before journaling.
- Root cause: both flatten paths called `broker.close_position()` **before** `cancel_symbol_orders()`. With a resting bracket leg the shares are `held_for_orders`, so the position `DELETE` 403s. In CLOSE_SIGNAL (`bot.py` exit branch, no try/except) this crashes the tick and the exit never executes. In the KILL SWITCH path the 403 is swallowed but `halted=True` is still set — the kill switch would **silently fail to flatten** on a real loss breach (safety-critical).

**Change:** reversed the order at all three flatten sites — cancel the symbol's resting orders FIRST, then close the position: kill-switch path, CLOSE_SIGNAL exit path, and the manual `--flatten` path. Symbol-scoped (`cancel_symbol_orders` only touches the bot's own symbol) — no other account positions touched.

**Validation:** not a strategy change, so backtest is unaffected — confirmed `python3 backtest.py` output unchanged (Long-only risk 4% still 162%/Sharpe 0.89) and `bot.py`/`broker.py` compile+import clean.

**Other bots this run:** DO NOTHING. LABU/TQQQ (meanrev) correctly sat out — underlying RSI stayed 46–79, never near the <30 buy trigger. MSTR/PLTR/TNA (trend) correctly blocked — MSTR below a falling EMA, PLTR/TNA ADX 8–20 (< adx_min 25). SOXL & UPRO in a recent drawdown but flagged no structural problem — do not tune in a drawdown.
