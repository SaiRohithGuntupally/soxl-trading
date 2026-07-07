# Operator NOTES

Dated log of autonomous operator changes: what was seen, what changed, why.

## 2026-07-07 — ALL bots halted by portfolio breaker on an ALPACA-SIDE account anomaly. NO code/config change made (documentation only). HUMAN ATTENTION NEEDED.

**Bot:** account-wide (all 7 share one Alpaca paper account PA35A01C1X94). Root SOXL is the config, but the trigger is broker-side.

**What I saw (authoritative broker queries, not just review.py):**
- `GET /v2/account`: `equity`=`cash`=`portfolio_value` = **-107,170.25**, buying_power 0, long_market_value 0, NO positions. `last_equity` (07-06 close) = 90,433.08.
- Portfolio history: 07-06 close ~91,512 -> 07-07 open (13:30 UTC) **-107,170.25**, frozen there all day. An instantaneous ~-$197k discontinuity with no intermediate values.
- `GET /v2/account/activities?date=2026-07-07` (all types): **EMPTY**. `orders?status=all`: nothing after the 07-06 SOXL sell. So **zero orders, zero fills, zero activities today**.
- The UPRO position (bought 2026-06-12, 262 sh @ 138.97, held for weeks) **vanished between the 10:07 and 10:12 ticks with no sell order and no fill**.

**Diagnosis:** a real -$197k swing is impossible from our system — max exposure was one ~$37k UPRO position, no shorts, multiplier unchanged, and the bot placed NO orders today. Instantaneous frozen equity + a position disappearing with zero fills/activities = **Alpaca paper-account data corruption / glitch (or an external reset)**, outside the bot's control.
- The journaled **-37,570.80** "loss" is a phantom: `soxl_daily_pnl` (bot.py:117) = `mv(0) + net_cashflow(0) - day_start_value(37,570.8)` = -37,570.8, because the position vanished with no offsetting fill for the cashflow method to capture. That tripped the PORTFOLIO breaker (>-15% of 91,391) and **correctly** halted all 7 bots.

**Change:** NONE. Rationale: (1) This is a broker-side data anomaly, not a strategy/structural defect to tune. (2) The breaker did its job — halting into an account showing -$107k equity / $0 BP is correct; trading is blocked regardless. (3) There is a latent fragility (phantom P&L when a close's FILL activity is missing/lagged — the 07-06 note already flagged activities can lag), but fixing it touches SAFETY-CRITICAL breaker logic that `backtest.py` does NOT exercise, based on one unreproducible corrupt-state event; hardening a breaker mid-incident on corrupt data risks WEAKENING it (forbidden). That fix belongs in a healthy account with a real reproduction/test, not reactively now. (4) Did NOT clear the halt or override the breaker (forbidden); it self-clears on the next new trading day via the date rollover in tick().

**Other bots:** LABU/MSTR/PLTR/TNA/TQQQ/SOXL all took ZERO positions today and are halted ONLY by the shared portfolio breaker — nothing symbol-specific. All correctly sat out per their gates.

**ACTION FOR HUMAN:** inspect/reset the Alpaca paper account — it reports negative equity with no trades behind it. Do NOT let the operator bot "trade out" of this or edit the kill-switch/P&L path to suppress the halt.

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
