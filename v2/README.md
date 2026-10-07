# fleet v2

One process, one tick, one ledger. Five trend bots sized off non-core capital, a
passive SPY core for the capital the bots leave idle, decisions once per day on the
near-complete bar, risk management every 15 minutes.

## Why v2 exists

The 2026-10-06 profitability review (`../research/PROFIT-REVIEW-2026-10-06.md`) found
three things in v1 worth acting on, and this app is those three things and nothing else:

| finding | v1 | v2 |
| --- | --- | --- |
| Signals were evaluated on the partial intraday bar every 15 min. 38% of trades were same-day round trips the backtest never saw. SOXL: -18% live-mode vs +61% end-of-day on a 10-year 30-min replay. | every tick decides | decide only in the last 20 min of the session; other ticks manage stops and kill switches only |
| 45% of days fully in cash, 34% average exposure. | idle | 50% SPY core, bots size off equity minus core (Sharpe 0.73 to 0.91, maxDD 45% to 30%, robust to +-20% on every parameter) |
| Mean-reversion bots: 8 trades in 10 years, nothing robust. | 7 bots | 5 trend bots |

Also: one SQLite ledger instead of 7 x 4 flat files, guardrails hard-capped in code,
a v1-collision guard (refuses to trade while v1 heartbeats are fresh), heartbeat and
healthchecks ping built in, 17 synthetic unit tests.

## Honest expectation

Read section 5 of the review first. On 10 years of data the Sharpe confidence interval
is about +-0.6, so the fleet and SPY are not statistically distinguishable. Half of this
portfolio is simply long the market; in 2022 the validated variant still lost 23%. The
trend sleeve has no demonstrated out-of-sample edge; it is kept because it is cheap
(low turnover) and it was the best-supported overlay tested. Expected forward result:
SPY-like return with a lower drawdown. If you want more than that, nothing tested in
the review delivers it without leverage, and leverage was shown to be leverage, not edge.

## Commands

```
python3 fleet.py tick                # one tick (cron every 15 min, weekdays)
python3 fleet.py tick --dry-run      # decide, place nothing
python3 fleet.py status [--json]     # account, core, bots, gates, decisions, verdict
python3 fleet.py flatten [SYM ...]   # close bot positions (never the core), halt today
python3 fleet.py backtest            # reproduce the validated numbers for THIS config
python3 fleet.py install-cron        # retire every v1 cron line, install the v2 line
python3 -m unittest discover -s v2/tests
```

## Deploying on the Pi (replaces v1)

```
cd ~/soxl-trading && git pull --ff-only
python3 -m unittest discover -s v2/tests        # must pass
python3 v2/fleet.py status                      # must reach Alpaca; verdict will say v1 is alive
python3 v2/fleet.py install-cron                # removes v1 lines, installs v2
```
v1 positions in SOXL/UPRO/MSTR/PLTR/TNA, if any, are adopted by v2 (it re-arms a stop
on anything naked). v2 refuses to trade while any v1 heartbeat is under 30 minutes old,
so after `install-cron` the first live tick happens once the last v1 tick has aged out.
Keep `HEALTHCHECK_URL` in `../.env`; v2 pings it every tick.

## Config

`config.json`: `account` caps (gross 100%, portfolio breaker 15%, per-bot kill 10%,
position 50% of non-core), `core` (symbol, fraction, rebalance band in equity points),
`defaults` applied to every bot, and the `bots` list. Hard ceilings are enforced in
`engine.load_config` regardless of the file. Event gating is off by default because the
review found the FOMC/CPI gate fitted after the fact.

Change the config, then run `python3 fleet.py backtest` and compare to variant D in
the review before deploying. That is the contract.

## Files

| file | role |
| --- | --- |
| `engine.py` | the tick: rollover, P&L, kill switches, stop management, decision window, sizing, core |
| `ledger.py` | SQLite: durable state (`kv`), `ticks`, `decisions`, `orders`, `equity_daily` |
| `fleet.py` | CLI |
| `backtest.py` | config to research-engine mapping, prints FULL / BACK-OOS / TUNE vs v1 and SPY |
| `run.sh`, `install_cron.sh` | host scripts |
| `tests/test_engine.py` | synthetic, no-network tests |

Reuses `../broker.py` (Alpaca client, Wilder indicators, audited 2026-10-06) and
`../notify.py` (Signal, best-effort).
