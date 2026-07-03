# Operator NOTES

Dated log of autonomous operator changes: what was seen, what changed, why.

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
