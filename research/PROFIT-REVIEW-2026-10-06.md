# Profitability review, 2026-10-06

Scope: can the 7-bot paper fleet (SOXL/MSTR/PLTR/TNA/UPRO trend, TQQQ/LABU mean-reversion)
be made to earn more, in absolute and risk-adjusted terms, with changes that survive
out-of-sample testing? Every number below is from `research/*.py`, run on Alpaca SIP daily
bars 2016-01-04 to 2026-10-06 (2705 sessions) plus 30-minute SIP bars for the intraday
study, with the same 10 bps per-side cost `backtest.py` uses. Nothing here placed an order
and no live config or `bot.py` was edited.

Why 2016: all prior research (`backtest.py`, `experiments.py`, `walkforward.py`) used the
IEX feed, which only starts 2020-07-27 for SOXL. The SIP feed adds 4.6 years
(2016-01-04 to 2020-08-27) that no parameter choice ever touched. That window is used as a
true backward out-of-sample test throughout, labelled BACK-OOS. The window the parameters
were tuned on (2020-08-28 to 2026-06-12) is labelled TUNE. "Fleet" means all 7 bots on one
shared 100k account with the live rules that `backtest.py` never modelled: every bot sizes
off total equity, 50% max position, cash constraint, 15% portfolio breaker, 10% per-symbol
kill switch. The new engine reproduces `backtest.py` on the IEX window (166% / Sharpe 0.91 /
50 trades vs 162% / 0.89 / 50).

## 1. Executive summary: the honest base case

The fleet is not a return engine and, measured at fleet level, it is not a drawdown shield
either. Over 10.75 years it earned a 17.1% CAGR with a 44.8% maximum drawdown (Sharpe 0.73,
MAR 0.38), versus SPY buy-and-hold at 15.2% CAGR / 33.8% drawdown (Sharpe 0.90, MAR 0.45)
and QQQ at 20.7% / 35.0% (0.96 / 0.59). A QQQ position timed by a 200-day SMA, with no bots
at all, beats the whole fleet on every risk-adjusted measure (15.9% CAGR, 23.3% drawdown,
Sharpe 0.98, MAR 0.68).

All of the fleet's excess return comes from the window the parameters were tuned on. On the
4.6 years the parameters never saw, the fleet returned -2% with a 30% drawdown while SPY
returned +87%. The expected forward result, before any change, is therefore SPY-like or worse
risk-adjusted performance with lower absolute return in bull markets, because the fleet
averages 34% gross exposure and sits in cash 45% of all days. The live window agrees:
model -1% for 2026-06-13 to 10-06 (SOXL -$424 realized, UPRO +$77; the host was dead from
July 8, so the live number is not a strategy result).

| window | fleet (live rules) | SPY B&H | QQQ B&H |
|---|---|---|---|
| FULL 2016-2026: CAGR / maxDD / Sharpe / MAR | 17.1% / 44.8% / 0.73 / 0.38 | 15.2% / 33.8% / 0.90 / 0.45 | 20.7% / 35.0% / 0.96 / 0.59 |
| BACK-OOS 2016-01 to 2020-08 | -0.5% / 30.1% / 0.08 / -0.02 | 14.5% / 33.8% / 0.83 / 0.43 | 24.5% / 28.6% / 1.12 / 0.86 |
| TUNE 2020-08 to 2026-06 | 34.0% / 44.8% / 1.12 / 0.76 | 15.5% / 24.5% / 0.93 / 0.63 | 17.7% / 35.0% / 0.83 / 0.50 |
| 2022 (return) | -25.2% | -18.7% | -33.2% |
| 2018 (return) | -16.9% | -5.0% | -0.1% |

The 44.8% fleet drawdown ran from 2021-02-09 to 2023-05-15 and was not recovered until
2024-02-22; SPY's drawdown over the same period was 25%. Seven correlated long-leveraged bots
that each size off total equity are one position, and at the worst moments gross exposure
reached 100% (200% if the paper account's 2x buying power is used, which it is live).

Per bot, standalone, Sharpe FULL / BACK-OOS / TUNE: SOXL 0.41 / -0.23 / 0.82; MSTR
0.45 / -0.95 / 0.85; PLTR 0.57 / n.a. / 0.79; TNA 0.19 / 0.18 / 0.18; UPRO 0.47 / 0.84 / 0.23;
TQQQ 0.30 / 0.15 / 0.76 on 6 trades in 10.75 years; LABU 0.04 / -0.31 / 0.61 on 2 trades.
Only UPRO has a positive backward OOS Sharpe, and it is the weakest bot in the tuning window.
This is what weak edges look like: wherever one window is good, the other is not.

What this review found, in rank order:

1. The live bot evaluates entries and exits on the partial, in-progress daily bar at every
   15-minute tick. Replaying 30-minute bars through the live indicator code shows this is
   the single largest controllable drag: for SOXL it turns +61% (evaluate once near the
   close) into -18%, doubles the trade count and creates 35 same-day round trips. This is
   an implementation change, not a strategy change, and it holds in both windows.
2. Idle cash is the biggest drag on absolute return, but the fix is beta, not alpha: a 50%
   SPY core with the bots sized off the non-core half lifts Sharpe from 0.73 to 0.91, MAR
   from 0.38 to 0.53, cuts max drawdown from 44.8% to 30.2%, and is positive in the backward
   OOS window (Sharpe 0.63 vs 0.08). It costs return in the tuning window. It is robust to
   core fraction (30/50/70%) and to +-20% on every trend parameter.
3. Retire the two mean-reversion bots. They made 8 trades in 10.75 years, no variant tested
   (2-day RSI, pullback-to-EMA, regime on/off) is robust to +-20% perturbation, and removing
   them changes fleet results by less than one Sharpe hundredth while removing two processes.

Everything else tested (ADX, EMA, chandelier and stop retunes, re-entry cooldowns, exit
rules, portfolio vol targeting, market-vol scaling, trading the underlying on margin,
risk parity, subset selection, higher risk, event gating, tighter breakers) either fails
out of sample, is not robust to perturbation, or is leverage dressed as edge. Details and
numbers in section 4.

## 2. Ranked recommendations

### 2.1 Evaluate signals on completed bars, not on the partial bar at every tick (implementation)

`bot.py` fetches 1Day bars during market hours; Alpaca includes today's in-progress bar, so
`ema_pair`, `adx` and the `u_close > ema` exit are computed with an incomplete close at
09:45, 10:00, ... This means a morning pop above the EMA opens a position and an 11:00 dip
closes it. `backtest.py` and `walkforward.py` assume signals on completed closes with a fill
at the next open, so the live bot has never been running the thing that was backtested.

`research/rq3_intraday.py` replays 30-minute SIP bars 2016-06 to 2026-10 through the live
functions (`broker.ema_pair`, `broker.adx`, `broker.atr` on a 110-bar window, 1.5 ATR stop,
3 ATR chandelier, 50% cap, 10 bps) in three modes. `live` evaluates at every 30-minute tick
on the partial bar; `eod` evaluates once at the last tick (the 15:45 tick in practice) and
fills at that price; `nextopen` is the backtest convention.

| bot | mode | return | CAGR | maxDD | Sharpe | MAR | trades | same-day round trips | BACK-OOS Sharpe | TUNE Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|
| SOXL 4% | live (current) | -18% | -2.0% | 44.6% | -0.01 | -0.04 | 132 | 35 | -0.57 | 0.27 |
| SOXL 4% | eod | 61% | 4.8% | 30.6% | 0.34 | 0.16 | 79 | 0 | -0.07 | 0.57 |
| SOXL 4% | nextopen | 35% | 3.0% | 36.6% | 0.25 | 0.08 | 79 | 8 | -0.41 | 0.63 |
| UPRO 2% | live (current) | 39% | 3.2% | 16.7% | 0.45 | 0.19 | 144 | 73 | 0.70 | 0.33 |
| UPRO 2% | eod | 51% | 4.1% | 15.5% | 0.55 | 0.26 | 69 | 0 | 0.82 | 0.38 |
| UPRO 2% | nextopen | 35% | 3.0% | 22.3% | 0.41 | 0.13 | 70 | 7 | 0.67 | 0.25 |
| TNA 2% | live (current) | 10% | 1.0% | 15.8% | 0.18 | 0.06 | 80 | 30 | 0.28 | 0.09 |
| TNA 2% | eod | 8% | 0.7% | 18.2% | 0.14 | 0.04 | 42 | 0 | 0.24 | 0.05 |
| MSTR 2% | live (current) | 67% | 5.1% | 21.2% | 0.49 | 0.24 | 119 | 44 | -0.88 | 0.87 |
| MSTR 2% | eod | 60% | 4.7% | 22.2% | 0.46 | 0.21 | 61 | 0 | -0.98 | 0.85 |

Reading: 38% of all live-mode trades (182 of 475) are same-day round trips that the backtest
never saw. The damage is concentrated in the two most volatile index ETFs, and SOXL is the
bot carrying 4% risk, so at fleet level this is where the money is. TNA and MSTR are a wash
(differences inside noise), so the change is not a universal improvement; it is the removal
of a cost the backtest did not model. Robustness: SOXL and UPRO improve in both windows and
on every measure; no bot gets materially worse. Note the daily-bar engine cannot see this
effect at all (close fill vs next-open fill is a wash at fleet level: Sharpe 0.74 vs 0.73,
BACK-OOS 0.22 vs 0.08, TUNE 1.05 vs 1.12), which is exactly why it went unnoticed.

The current numbers also quantify what the fleet actually ran: SOXL in live mode is the
negative-Sharpe strategy, so the -$424 live result is consistent with the model, not bad luck.

Proposed change (code, for the owner / the bot.py agent; not applied here). Either of:

(a) In `tick()`, drop the in-progress bar from `ubars` (and from the SPY bars in
`_mkt_confirm`) unless the tick is the last one of the session, so entry and exit are
evaluated on completed closes except at 15:45 ET, where the near-complete bar is used.
Trailing-stop management and the kill switch keep running every 15 minutes.

(b) Config-only approximation via the crontab, with a real trade-off: run each trend bot
once a day at 13:45 MT (15:45 ET) instead of every 15 minutes. The server-side bracket stop
still protects the position intraday; the chandelier ratchet becomes once a day, which is
what `backtest.py` assumes anyway; the 10% per-symbol kill switch and 15% breaker become
once a day, and neither has fired once in 10.75 years of simulation (section 4.9).

```
# install_cron.sh gen(), option (b): one decision tick per day for the trend bots
-  echo "*/15 * * * 1-5 $R/run_tick.sh $TAG SOXL"
+  echo "45 13 * * 1-5 $R/run_tick.sh $TAG SOXL"
   local m=3
   for b in MSTR PLTR TNA UPRO; do
-    echo "$m,$((m+15)),$((m+30)),$((m+45)) * * * 1-5 $R/run_bot.sh $R/bots/$b/config.json $TAG $b"
+    echo "$((45+m/3)) 13 * * 1-5 $R/run_bot.sh $R/bots/$b/config.json $TAG $b"
     m=$((m+3))
   done
```

Option (a) is better because it keeps the 15-minute safety ticks; it needs a `--manage-only`
style guard or the completed-bar slice in `bot.py`.

### 2.2 Put idle capital in a core index holding and size the bots off the rest (allocation)

The fleet averages 34% gross exposure. `research/rq1_capital.py` and `rq6_candidates.py`
compare the fleet with and without a core holding. "Non-core sizing" means the bots size
their risk off `equity - core value`, which keeps total gross exposure at or below 100%.
Letters refer to the rq6 variants; all include the completed-bar fix only in the form the
daily engine can express (close fill), so the gain in 2.1 is additional.

| variant | FULL CAGR / maxDD / Sharpe / MAR | BACK-OOS CAGR / Sharpe / MAR | TUNE CAGR / Sharpe / MAR | 2022 | turnover |
|---|---|---|---|---|---|
| A live fleet | 17.1% / 44.8% / 0.73 / 0.38 | -0.5% / 0.08 / -0.02 | 34.0% / 1.12 / 0.76 | -25% | 18.0x |
| I live fleet + 50% SPY core, non-core sizing | 16.1% / 28.4% / 0.92 / 0.57 | 7.8% / 0.54 / 0.27 | 23.4% / 1.18 / 0.84 | -21% | 8.2x |
| D 5 trend bots + 50% SPY core, non-core sizing | 16.1% / 30.2% / 0.91 / 0.53 | 9.2% / 0.63 / 0.36 | 22.1% / 1.08 / 0.73 | -23% | 8.7x |
| F 5 trend bots + 70% SPY core, non-core sizing | 15.8% / 28.5% / 0.95 / 0.55 | 11.4% / 0.75 / 0.40 | 19.3% / 1.09 / 0.70 | -21% | 5.2x |
| G 5 trend bots + 50% QQQ core, non-core sizing | 19.0% / 36.7% / 0.96 / 0.52 | 15.4% / 0.90 / 0.72 | 22.0% / 1.02 / 0.60 | -31% | 7.3x |
| E 50% SPY core but bots keep sizing off total equity | 19.2% / 39.5% / 0.84 / 0.49 | 8.9% / 0.52 / 0.29 | 28.2% / 1.05 / 0.71 | -33% | 11.8x |
| H 50% SPY core timed by SMA200 | 16.0% / 37.2% / 0.84 / 0.43 | 6.7% / 0.46 / 0.31 | 24.1% / 1.08 / 0.65 | -28% | 12.5x |
| SPY B&H | 15.2% / 33.8% / 0.90 / 0.45 | 14.5% / 0.83 / 0.43 | 15.5% / 0.93 / 0.63 | -19% | 0 |
| QQQ B&H | 20.7% / 35.0% / 0.96 / 0.59 | 24.5% / 1.12 / 0.86 | 17.7% / 0.83 / 0.50 | -33% | 0 |

Anchored walk-forward across these variants (select by in-sample MAR each year, test on the
following year, 2019-2026): stitched OOS Sharpe 0.93, maxDD 36.7%; the fixed variant D over
the same period scores Sharpe 1.05, MAR 0.69, maxDD 30.2% versus the live fleet's 0.94 /
0.57 / 44.8% and SPY's 0.95 / 0.52 / 33.8%.

Whole-package perturbation of D (every trend parameter moved +-20% on every bot, all up,
all down): FULL Sharpe stays in 0.89 to 1.02, BACK-OOS Sharpe in 0.53 to 0.78, TUNE Sharpe in
1.00 to 1.25, 2022 return in -34% to -16%. Nothing flips sign. The same perturbation on the
fleet without a core spans BACK-OOS Sharpe -0.00 to 0.60.

Honest framing. This works because half the money is simply long the market; the bots add
a modest overlay. The core is beta: in 2022 variant D still lost 23% (SPY -19%), and a QQQ
core is a sector bet that paid in this decade and need not again. The timed core (H) is worse
than the untimed one in every window because the SMA200 whipsaws cost more than the 2022
protection was worth in this sample. Sizing the bots off total equity while holding a core
(E) is the one variant that raises drawdown, because it lets gross exposure exceed 100%.
If the owner wants the market exposure, the cleanest version is the owner's own decision to
hold SPY; the bots then need one code change so they do not count the core as risk capital.

Proposed change (code; not applied): in `bot.py` `tick()`, compute
`size_equity = equity - core_market_value` where the core is identified by a config key
such as `"core_symbols": ["SPY"]`, and pass `size_equity` to `compute()` and to the
`max_position_pct` cap. Buying power already stops the bots from spending the core.
Config-only approximation if no code change is wanted: hold the core and halve every
`risk_pct` (SOXL 4.0 -> 2.0, others 2.0 -> 1.0) so that risk per trade as a fraction of
non-core capital is unchanged; this reproduces D to within rounding because sizing is linear
in `risk_pct`.

### 2.3 Retire the mean-reversion bots (TQQQ, LABU)

`research/rq4_meanrev.py`. The live rule (QQQ or XBI RSI14 < 30 while above its 200-day
SMA, exit RSI > 55, 2 ATR stop, 2R target) fired on 7 days (TQQQ) and 3 days (LABU) in 2705
sessions. Standalone: TQQQ 8% total return, Sharpe 0.30, 6 trades; LABU 0%, Sharpe 0.04,
2 trades. These bots were also running with the regime gate silently disabled until
2026-10-06, so even their tiny live history is not of this strategy.

Alternatives tested: RSI14 without regime, RSI2 < 10 (Connors style, exit RSI2 > 65 or close
above SMA5) with and without regime, RSI2 < 5, 10-day max hold, pullback to EMA20 inside the
regime (with and without a 3% depth), and the chandelier exit `analyze.py` used.

| variant (TQQQ / QQQ signal) | FULL Sharpe / MAR / trades | BACK-OOS Sharpe | TUNE Sharpe |
|---|---|---|---|
| live RSI14 < 30, regime | 0.30 / 0.16 / 6 | 0.15 | 0.76 |
| RSI14 < 30, no regime | 0.58 / 0.32 / 17 | 0.67 | 0.51 |
| RSI2 < 10, regime | 0.32 / 0.12 / 96 | 0.14 | 0.52 |
| pullback close < EMA20, regime | 0.28 / 0.14 / 167 | 0.21 | 0.36 |
| QQQ buy and hold | 0.96 / 0.59 | 1.12 | 0.83 |

Walk-forward selection among all variants (2019-2026) gives Sharpe -0.02 for TQQQ, 0.41 for
LABU (chosen variant: a 3% pullback with 38 trades in 10 years) and 0.30 for UPRO. The +-20%
perturbation of RSI2 flips sign on TQQQ (all parameters -20%: Sharpe -0.30), and the live
RSI14 variant's best perturbation (rsi_sell 44) is a 6-trade sample. The "RSI14 no regime"
row looks best on paper but it is 17 trades, and buying oversold dips with no regime filter
on a 3x ETF is how 2022-style drawdowns happen (the regime filter exists for a reason that
10 years of data cannot test).

Decision rule applied: no variant is robust OOS, trade counts are too small to distinguish
from zero, and the fleet without the two bots is indistinguishable from the fleet with them
(FULL Sharpe 0.72 vs 0.73, TUNE 1.07 vs 1.12, BACK-OOS 0.12 vs 0.08). Retiring them removes
two cron processes, two state files and two places for the next silent bug. There is no
evidence they add anything.

Proposed change (config / cron only):

```
# install_cron.sh gen(): remove
-  echo "1,16,31,46 * * * 1-5 $R/run_bot.sh $R/bots/TQQQ/config.json $TAG TQQQ"
-  echo "4,19,34,49 * * * 1-5 $R/run_bot.sh $R/bots/LABU/config.json $TAG LABU"
# and drop TQQQ LABU from the config existence check on the line below gen()
```

### 2.4 Minor: keep gross exposure at or below 100% of equity

The paper account grants 2x buying power and nothing in the fleet caps the sum of positions.
Simulated with 2x available: FULL Sharpe 0.70 vs 0.73, maxDD 49.4% vs 44.8%, BACK-OOS maxDD
42.1% vs 30.1%, for +0.4 points of CAGR. Max gross hit 200%, p90 105%. Recommendation 2.2
(non-core sizing) removes this by construction; without it, the config-only lever is
`max_position_pct` (50 -> 25 on every bot caps the sum at 175% in the worst case and ~100%
in practice) or a code cap on total fleet notional. Low priority, listed for completeness.

## 3. What not to change, with the numbers

The research also confirms three things the fleet already does and should keep.

The 15% portfolio breaker and 10% per-symbol kill switch never fired in 10.75 years at 1x
gross (0 and 0 events); at 2x gross the 10% breaker fired twice. They are inert backstops
with zero cost. Do not tighten them: at 5% the breaker fires 30 times and costs 55 points of
total return (443% -> 388%) with no drawdown benefit (43.4% vs 44.8%). The breaker locks in
losses on exactly the days a trend bot should be holding; it is insurance against operational
disasters like 2026-07-07, not a strategy tool.

The SPY confirm (`mkt_confirm`) and the event gate are not worth touching. The 1-day FOMC
block changes fleet Sharpe from 0.73 to 0.72 (CPI and NVDA dates were not reconstructed;
FOMC is the dominant scheduled event). A 3-day block looks better in the full sample (0.84)
and in BACK-OOS (0.35) but not in TUNE (1.11 vs 1.12) and it is one more knob fitted after
the fact; not recommended without the full event history.

EMA20 / ADX25 / chandelier 3 / stop 1.5 remain at a local optimum in the sense that no
neighbour is robustly better across bots (section 4.1 to 4.4). The +-20% perturbation on
SOXL alone is fragile (ADX 30 cuts FULL Sharpe from 0.41 to 0.15; BACK-OOS flips sign on five
of ten perturbations), which is another way of saying the SOXL edge is thin, not that a
different setting is known to be better.

## 4. Rejected ideas

Each line: what was tested, the best-looking number, why it is rejected. Full tables are in
`research/.cache/*.out` after running the scripts.

4.1 ADX threshold (`rq3_entries.py` C). Removing the ADX filter on SOXL looks spectacular
in the full sample (272% vs 84%, BACK-OOS Sharpe +0.39 vs -0.23) but the same change makes
TNA (-19%), MSTR (BACK-OOS -1.04) and PLTR (0.34 vs 0.57) worse, there is no plateau (ADX 20
on SOXL: 123%, DD 47%), and anchored walk-forward on SOXL picks a path that beats the fixed
ADX 25 by 0.04 Sharpe (0.69 vs 0.65) with a worse drawdown (35% vs 29%). Not robust across
bots, not a plateau.

4.2 EMA length (E). No plateau on any bot. SOXL: 20 is best FULL (0.41) and worst BACK-OOS
among 20/30/100; UPRO: EMA100 is best FULL (0.62) and worst BACK-OOS (0.45 vs 0.84).
Walk-forward selection on UPRO ends at Sharpe -0.06. Rejected.

4.3 Chandelier and initial stop (F). Chandelier 2 improves SOXL (0.44 / DD 27%) and TNA
and worsens MSTR (0.34); chandelier 4 does the reverse. stop_atr 1.0 again raises return
through size (SOXL DD 56%). Walk-forward on SOXL: 0.61 vs fixed 0.65. Rejected, as the
earlier NOTES.md conclusion already said.

4.4 Exit rule (D): chandelier only, EMA break only, SMA50 break. Chandelier-only helps SOXL
(152% vs 84%, BACK-OOS +0.39) and UPRO (BACK-OOS 0.95 vs 0.84) and hurts MSTR (0.27 vs 0.45)
and TNA (0.12 vs 0.19); EMA-only helps PLTR (0.82 vs 0.57) and nobody else. Walk-forward
across exit rules: SOXL 0.66 vs fixed 0.65, MSTR 0.62 vs 0.68, TNA -0.04 vs 0.19. No
consistent winner; the variance between bots is larger than the effect. Rejected.

4.5 Re-entry after a stop-out (B): cooldown 3/5/10 days or require a fresh EMA cross.
Cooldown 10 is the best UPRO variant (Sharpe 0.69 vs 0.47, DD 12% vs 21%) and the worst
SOXL variant (0.22 vs 0.41); fresh-cross is worst for SOXL, MSTR and UPRO. Walk-forward on
SOXL: 0.54 vs fixed 0.65. Whipsaw re-entry is a real cost on UPRO only; not generalisable.
Rejected as a fleet change (UPRO-only tuning on 74 trades is curve fitting).

4.6 Portfolio-level ex-ante vol cap (`rq5_big_ideas.py` a). Caps of 10% to 40% annualised
with correlation assumed 1. Every cap lowers Sharpe (0.73 -> 0.54 to 0.64) and MAR (0.38 ->
0.24 to 0.30); the drawdown falls (44.8% -> 15.5% to 40.7%) but only because exposure falls
(34% -> 10% to 27%). This is deleveraging, which the owner can do with `risk_pct`. Rejected
as a Sharpe improvement; the existing per-trade ATR sizing is already a vol target.

4.7 Market-vol scaling of all risk (b): `risk x clamp(target / SPY 20-day vol)`. Sharpe
falls in FULL (0.66 to 0.71 vs 0.73) and BACK-OOS (-0.15 to -0.01 vs 0.08) for every target
and cap; only the 20% / 1.5x (levers up in calm markets) variant beats baseline in TUNE
(1.16 vs 1.12) and it is the worst in BACK-OOS. Rejected.

4.8 Trading the underlying instead of the 3x ETF (c). Same signals, same risk sizing, 7%
margin interest on negative cash. SOXX 1x: Sharpe 0.43 vs SOXL 0.41, return 42% vs 84%
(half the risk, half the return). SOXX at 2x notional: 84% / DD 36% / Sharpe 0.43, identical
to SOXL (84% / 34.5% / 0.41). SOXX at 3x needs portfolio margin and is worse (DD 52%,
Sharpe 0.39). Same pattern on UPRO/SPY, TNA/IWM, TQQQ/QQQ: the ETF's embedded leverage is
cheaper than retail margin and the decay the bot avoids by holding only in trends is small.
Rejected; the 3x ETFs are the right instrument for this sizing rule.

4.9 Breakers (`rq2_portfolio.py`). 15% / 10%: never fire; 5% / 5%: 30 trips, -55 points of
return, drawdown unchanged. Covered in section 3. Tightening rejected; loosening pointless.

4.10 Fewer bots / higher conviction (`rq2_portfolio.py`). SOXL-only at 4%: BACK-OOS -19%,
TUNE +131%. UPRO-only at 2%: BACK-OOS +30% (Sharpe 0.84), TUNE +10% (0.23). SOXL+UPRO: BACK
+6%, TUNE +138%, Sharpe 0.50 FULL. No subset has a FULL Sharpe above 0.73 except "all 7 at
4%" (0.87) and that is leverage: return 1061%, maxDD 53.7%, 2022 -32.6%, BACK-OOS Sharpe
0.22. Anchored walk-forward over subsets (select by IS MAR): stitched OOS Sharpe 0.79 with a
52.4% drawdown, versus 0.94 / 44.8% for the fixed fleet and 0.95 / 33.8% for SPY. Selecting
bots on history does not work here because the per-bot edges do not persist. Rejected.

4.11 Risk parity across bots (walk-forward, risk inversely proportional to each bot's
standalone vol measured only on prior data, budget held at the live 16 risk points, 4% cap).
2018-2026: Sharpe 0.80 vs 0.79 live, maxDD 28.9% vs 44.8%, return 183% vs 381%. The weights
push 7 points into LABU, which does not trade, so this is a 50% deleveraging with equal
Sharpe. Legitimate if the owner wants a smaller drawdown, but it is not an improvement in
edge and recommendation 2.2 achieves a smaller drawdown with a higher Sharpe and more return.

4.12 Mean-reversion variants (section 2.3 and `rq4_meanrev.py`): RSI2, RSI14 without regime,
pullback-to-EMA, chandelier exit, max hold. None robust; see above.

4.13 Event gating, 3-day block (section 3). Looks good in two windows, flat in the third,
fitted after the fact. Rejected pending a full CPI / earnings calendar.

4.14 Using the 2x paper buying power (`rq5` e): Sharpe 0.70 vs 0.73, maxDD 49.4% vs 44.8%.
Rejected; see 2.4.

4.15 Same-day close fill as a daily-bar change (`rq3_entries.py` A, `rq6` B). Chosen every
year by walk-forward on SOXL and UPRO, improves 4 of 5 bots in the full sample and the
backward window (fleet BACK-OOS Sharpe 0.22 vs 0.08) but costs in the tuning window (1.05 vs
1.12) and the fleet-level FULL effect is 0.01 Sharpe. The daily engine cannot represent the
real issue (section 2.1); the recommendation is the intraday fix, not a change of fill
convention in the backtest.

## 5. Caveats

- All results are simulations with 10 bps per side, daily bars, and fills at the close, the
  next open, or the stop level; the intraday study fills at 30-minute bar closes. Live
  slippage on 3x ETFs around the open and close is larger. No number here is a forecast.
- Ten years and 328 trades is a small sample for a strategy family whose decade-long
  Sharpe is below 1. The confidence interval on a 0.73 Sharpe over 10.75 years is roughly
  +-0.6; the difference between the fleet and SPY buy-and-hold is not statistically
  significant in either direction. The backward OOS failure is the strongest single piece of
  evidence in this document and it says the tuned edge did not exist before 2020.
- The 2016-2020 window includes the 2018 Q4 and March 2020 crashes and a long semis bull run;
  the tuning window includes 2022 and the AI run. Neither is the next ten years.
- PLTR has data only from 2020-09-30 and therefore has no backward OOS test at all; its
  numbers are in-sample by construction.
- The intraday replay uses the live indicator code but not the live order path (bracket
  legs, held-for-orders retries, fill lag); those are correctness issues the concurrent
  bot.py audit addresses, not strategy issues.
- The core-holding result is market beta over a decade in which US equities compounded at
  15% to 21% a year. A decade with flat equities makes the fleet-plus-core look like the
  fleet, and the fleet alone has no demonstrated OOS edge.

## 6. Reproduce

```
python3 research/rq0_base.py        # base case, per bot and fleet, all windows, yearly table
python3 research/rq1_capital.py     # fleet vs fleet + core vs buy-and-hold
python3 research/rq2_portfolio.py   # subsets, walk-forward selection, risk parity, breakers
python3 research/rq3_entries.py     # entry fill, re-entry, ADX, exit rule, EMA, stops, perturbation
python3 research/rq3_intraday.py    # 30-minute replay of the live partial-bar logic (fetches ~70k bars per symbol once)
python3 research/rq4_meanrev.py     # TQQQ / LABU / UPRO mean-reversion variants
python3 research/rq5_big_ideas.py   # vol cap, vol scaling, underlying vs 3x, FOMC gate, 2x buying power
python3 research/rq6_candidates.py  # the recommended package at fleet level, walk-forward, perturbation
```

All scripts read `.env` through `broker.load_creds()` and make only GET requests to the
Alpaca data API; bars are cached under `research/.cache/` (gitignored). `research/common.py`
holds the shared engine (`simulate_fleet`), metrics, walk-forward and perturbation helpers.
Python 3.9, stdlib only.
