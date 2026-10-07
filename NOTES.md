# Operator NOTES

Dated log of autonomous operator changes: what was seen, what changed, why.

## 2026-07-07 (later run) — Anomaly UPDATE: account now shows FOREIGN positions + recovered equity. Still NO code/config change. HUMAN ATTENTION STILL NEEDED.

**Bot:** account-wide (shared Alpaca paper account). Follow-up to the 10:12 incident note below.

**What I saw (authoritative `GET /v2/account` + `/v2/positions`, ~14:00 UTC-6):**
- `equity`/`portfolio_value` = **+91,255.43** (recovered from the morning's frozen -107,170), `status`=ACTIVE, buying_power 1,930.
- But `cash` = **-107,170.27** and `long_market_value` = **207,035** — i.e. the morning's "-107k equity" was this negative cash; equity is now positive because the account is stuffed with margin-bought positions.
- Positions returned: **AVGO 19, NVDA 56, SMCI 447, VTI 89, WMT 960, XLK -48 (short)**, plus our **UPRO 262**. Six of these seven symbols are ones **NONE of our bots ever trade** (our universe is SOXL/UPRO/LABU/MSTR/PLTR/TNA/TQQQ). Our UPRO (262 sh, bought 06-12) is back.
- `portfolio.json` ledger is now normal (UPRO -314, all others 0) — the phantom -37,571 is gone; the stale -37,571 halt persists only in each bot's `state.json` for the rest of the day.

**Diagnosis:** confirms the 10:12 note — this is an **external/broker-side account event**, not a strategy defect. The paper account is contaminated with positions our system never placed (external use/reset of PA35A01C1X94). The -107k negative cash is from those foreign margin buys, not our trading.

**Change:** NONE. Rationale: (1) not a strategy/structural problem to tune — do not tune in a drawdown/anomaly. (2) Did NOT clear the halt or weaken the breaker (forbidden); it self-clears on the next new trading day. (3) Did NOT flatten anything — the six foreign symbols are not ours to touch (hard rule: only ever trade each bot's own symbol), and our UPRO has no exit signal and is correctly frozen under the halt. All 7 bots: DO NOTHING.

**ACTION FOR HUMAN (unchanged, now with more evidence):** the shared paper account holds positions our bots never opened and carries -107k cash. Inspect/reset the Alpaca paper account and stop whatever external process is trading into it. Do NOT let the operator override the breaker or "trade out" of this.

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

## 2026-07-08 — root SOXL: event-calendar maintenance (drop past, add BLS-confirmed CPI)

**Bot:** root SOXL (event_dates lives only in the root config; OPERATOR.md's event-calendar duty is SOXL-scoped).

**Review of all 7 bots this run — NO strategy change (disciplined, not busy):**
- The `11 KILL_SWITCH / 45 HALTED` signature is IDENTICAL across all 7 bots — it is the already-documented 2026-07-07 Alpaca-side account anomaly (portfolio breaker), handled with NO code/config change. It self-cleared on the date rollover: today all bots show `halted: false`. The per-bot KILL_SWITCH flags are stale artifacts of that external incident, not new per-bot risk problems — do NOT lower risk_pct off them.
- LABU/MSTR/PLTR/TNA/TQQQ: zero entries again — correct gate sit-out (documented pattern), not structural.
- UPRO: holding 262 sh, unrealized -514.83, today -568.54 realized → a drawdown. Do NOT tune in a drawdown.
- SOXL: flat, FLAT_NO_SIGNAL, no structural flag.

**Change (non-strategy, backtest-independent — event_dates only blocks new entries near scheduled events; touches no tunable knob):** refreshed root config `event_dates`. Dropped past `2026-06-17` (FOMC). Added BLS-confirmed CPI release dates `2026-09-11`, `2026-10-14`, `2026-11-10` (Aug 12 already present). Did NOT add NVDA Q3 FY27 (November 2026, exact date not yet announced) or Dec 2026 CPI (BLS not yet posted) — no guessing dates. Forward calendar now: 07-14 CPI, 07-29 FOMC, 08-12 CPI, 08-26 NVDA, 09-11 CPI, 09-16 FOMC, 10-14 CPI, 10-28 FOMC, 11-10 CPI, 12-09 FOMC.

**Validation:** `python3 -c json.load` parses clean; guardrails unchanged (risk_pct 4.0, max_daily_loss_pct 10.0, portfolio_max_loss_pct 15.0). No strategy logic changed → backtest unaffected. Sources: BLS CPI release schedule (bls.gov/schedule/news_release/cpi.htm).

## 2026-10-06 — fleet found DEAD since 07-08; monitoring + regime-gate fix (Mac, human-initiated)

**Evidence:** Alpaca: last fleet order 2026-07-08 (UPRO sell), zero orders/activities since; no fleet
positions; last Pi push 07-08. On 10-06 MSTR (ADX 39.5) and PLTR (ADX 28.8) both passed their entry
gates with SPY confirm OK — a live fleet would have entered. Pi unreachable from the Mac (off LAN).
Live window realized: SOXL 34 sh 215.20 -> 202.72 (-$424; SOXL later fell to ~164, exit worked),
UPRO 262 sh 138.97 -> 139.26 (+$77).

**Root cause of the 3-month blind spot:** every alert path (Signal, operator) lives ON the Pi; absence
of a daily summary went unnoticed. No external dead-man's switch, no one-command status.

**Changes (ops + one correctness fix, no strategy knobs touched):**
- `heartbeat.sh` sourced by `run_tick.sh`/`run_bot.sh`/`operator.sh`: stamps `heartbeat/<bot>` and
  pings `HEALTHCHECK_URL` (healthchecks.io) when set -> external alert when the host dies.
- `fleet_status.py`: fleet health from anywhere; `STALE` verdict when no fleet order for N days while
  bots are enter-eligible and flat; `--notify` cron'd 14:30 MT as an on-host watchdog.
- `install_cron.sh`: idempotent full crontab (also finally installs the pending tracker crons);
  `doctor.sh`: on-host PASS/FAIL checklist for recovery.
- **bot.py bug fix:** `daily_bars` fetched a fixed 160 calendar days (~110 bars) but TQQQ/LABU use
  `regime_ma: 200`, so `sma()` returned None and `regime_ok` **failed OPEN** — the meanrev regime gate
  was silently disabled live (analyze.py's backtest REQUIRES the regime). Now `bars_lookback_days(cfg)`
  sizes the fetch per config (330 d -> 227 bars for a 200 SMA, verified live) and the gate fails CLOSED
  with `regime_unavailable` journaled. Trend bots unaffected (lookback stays 160 d).
- review.py: "Red days dominate" needed red>=3 (fired on 1 red/0 green).

**Validation:** py_compile clean; `fleet_status.py` live run -> STALE (correct); TQQQ SMA200 now
670.08 vs close 759.62 (regime ok); `install_cron.sh --dry-run` renders 12 lines; backtest.py untouched.
**Pending on the Pi (human):** `git pull`, `./doctor.sh`, `./install_cron.sh`, set `HEALTHCHECK_URL`.

## 2026-10-06 (later) - fleet-wide correctness audit of the live code path (Mac, human-initiated)

**Scope:** bot.py / broker.py / portfolio.py / notify.py / review.py / tracker.py and all 7 configs, read against
the 07-03 / 07-06 / 07-07 incidents. Correctness only: no strategy knob, signal or guardrail was changed.
Nothing was sent to Alpaca except GETs; behaviour was verified with a synthetic no-network tick harness
(11 scenarios, scratch file, not committed).

**Evidence / defects found:**
- Naked position after a failed flatten (HIGH). `flatten_symbol` cancels the bracket legs FIRST; if the close
  then fails (the 07-06 403 pattern) and the exit signal flips back before the next tick, the HOLD branch kept
  the position with NO stop at all (`_manage_trailing` returned early when no stop order existed; meanrev bots
  never even looked). Fix: `_ensure_stop` runs on every HOLD; if the position has zero resting orders it re-arms
  a GTC sell-stop at the level remembered at entry (`state.stop_price`, new) lifted to the chandelier when
  trailing; if that level is already at/above the price it flattens instead. Skips when any order rests for the
  symbol (TP leg holding the shares, pending close) and for non-long positions.
- Phantom P&L when the FILL activity lags (HIGH, the 07-07 latent fragility). `soxl_daily_pnl` saw a stopped-out
  position as `pnl = -day_start_value` until the activity landed, which can falsely trip the per-symbol kill AND
  the 15% portfolio breaker for all 7 bots (UPRO alone was 36% of equity). Fix: `broker.todays_fills` reconciles
  activities against `GET /v2/orders?status=closed&symbols=X` by `filled_at` (ET date) and synthesizes any
  filled_qty the activities feed has not reported yet at `filled_avg_price`; best-effort, activities stand if the
  orders call fails. Verified order/activity field names against the live account (read-only).
- Live ATR was a simple mean of the last 14 TRs; backtest.py/analyze.py use Wilder ATR (MEDIUM). Stops and
  position sizes therefore differed from what was validated: on 2026-10-06 bars simple vs Wilder = SOXL
  11.34/11.36, UPRO 3.74/3.70, TQQQ 2.98/2.81, LABU 19.87/19.23, TNA 2.27/2.31, MSTR 9.19/8.69, PLTR 5.42/6.01
  (up to ~11%). Fix: `broker.atr` is now Wilder and equals `backtest.atr_series(...)[-1]` exactly on synthetic
  bars. backtest.py itself is untouched, so its numbers are unchanged.
- Kill-switch re-trip spam (MEDIUM). The kill branch sits before the HALTED short-circuit, so with a realized loss
  every later tick re-journaled KILL_SWITCH and re-paged Signal (the "11 KILL_SWITCH" signature on 07-07 and the
  inflated review.py KILL flag the operator is told to lower risk_pct on). Fix: once halted with no position the
  tick falls through to the quiet HALTED branch; if a position is still open the kill path still re-runs so the
  flatten is retried every tick (the 07-06 safety property is preserved).
- `--flatten` re-bought itself (MEDIUM). A manual kill closed the symbol but left state untouched, so the next
  cron tick (flat + entry_ok) re-entered within 15 min. Fix: `--flatten` now also marks the bot halted for the
  rest of the ET day (creating today's state with the pre-flatten position value if no tick has run yet).
- Trailing stop lagging a fast move (LOW/MEDIUM). If the chandelier was already above the price the PATCH is
  pointless (or rejected) and the exit the backtest takes never happened. Fix: `_manage_trailing` flattens and
  the tick journals CLOSE_TRAIL; it still never lowers a stop.
- Guardrail ceilings only in prose (LOW). `load_config` now hard-caps risk_pct at 4.0 and portfolio_max_loss_pct
  at 15.0 in code, like max_daily_loss_pct already was.
- review.py counted exits under the long-gone action name CLOSE_TREND_BREAK (trend_closes always 0, quick
  reversals never detected); tracker.py B&H benchmark fetched 40 days of bars for a window that started in
  June (wrong baseline); `unrealized_intraday_pl: null` would have crashed a tick. All fixed (LOW).

**Reported, deliberately NOT changed:** signal/EMA/ADX computed on the partial intraday IEX bar (design, matches
fleet_status); `latest_price` from the thin IEX tape for sizing; a manual position in a bot's symbol is treated
as the bot's own (including by `--flatten`); kill-switch denominator is whole-account equity incl. the foreign
AVGO/NVDA/SMCI/VTI/WMT/XLK positions; `get_open_orders` limit=100 is account-wide; chandelier uses current ATR
while the backtest freezes ATR at entry; alpaca_test.py keeps its own simple-mean ATR (diagnostic only).

**Validation:** py_compile clean (bot, broker, review, tracker); synthetic harness all green (Wilder ATR ==
backtest, fill reconciliation incl. orders-endpoint failure, caps, naked re-arm + level, no re-arm when a leg
rests, ratchet never loosens, crossed chandelier -> CLOSE_TRAIL, kill trips once then HALTED and retries flatten
while a position remains, rollover, --flatten halts, meanrev re-arm); `python3 fleet_status.py` read-only run
unchanged (STALE verdict, MSTR/PLTR enter-eligible, all gates compute). No orders placed.
