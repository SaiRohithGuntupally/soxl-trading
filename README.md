# soxl-trading

A self-contained **paper-trading** toolkit + autonomous bot for SOXL (3x semis ETF)
on the Alpaca paper API. Stdlib Python only — no pip installs.

> ⚠️ Paper money only. Not investment advice. A leveraged-ETF bot that rewrites its
> own strategy in response to losses is, by design, prone to curve-fitting — this is a
> learning sandbox, not a money printer. See `OPERATOR.md`.

## Pieces

| file               | what it does |
|--------------------|--------------|
| `position_size.py` | Fixed-risk position sizer (2% rule, ATR stops). Standalone CLI. |
| `broker.py`        | Alpaca paper-API client + ATR/EMA indicators (stdlib urllib). |
| `bot.py`           | One tick = check signal, trade SOXL, manage risk, log. |
| `review.py`        | Reads the journal + live account → performance summary + diagnosis flags. |
| `alpaca_test.py`   | One-shot connectivity + sizing sanity check. |
| `config.json`      | Tunable knobs + the hard kill-switch ceiling. |
| `trade-checklist.md` | The discretionary rules the bot automates. |
| `OPERATOR.md`      | Instructions for the autonomous review/edit loop. |

## Strategy (simple + auditable)

- **Signal:** trade the underlying **SOXX** — long only when price is above a *rising* EMA.
- **Size:** 2% account risk; stop = `stop_atr` × SOXL's *own* ATR (correct price scale).
- **Execute:** server-side **bracket** order (entry + stop-loss + take-profit at `tp_R`).
- **Exit:** trend gate fails while holding → flatten.
- **Kill switch:** SOXL-only daily loss ≥ `max_daily_loss_pct` (10%) → flatten SOXL + halt for the day.
- **Isolation:** the bot **only ever touches SOXL** — other paper positions are never touched, and P&L is measured on SOXL alone (cashflow method).

## Setup

```bash
cp .env.example .env      # add your Alpaca PAPER keys
python3 alpaca_test.py    # verify connectivity + sizing
```

## Run

```bash
python3 bot.py --status     # account + positions, no trading
python3 bot.py --dry-run    # one tick, decide but place NO order
python3 bot.py              # one tick, trades for real (paper)
python3 bot.py --loop 120   # local continuous mode, tick every 120s
python3 bot.py --flatten    # close ONLY the SOXL position now
python3 review.py           # performance review + diagnosis
```

## Operations (keeping the fleet alive)

The fleet ran silently dead from 2026-07-08 to 2026-10-06 and nobody noticed. These
exist so that cannot happen again:

| command | where | what |
| --- | --- | --- |
| `python3 fleet_status.py` | anywhere with `.env` | live fleet health: positions, last fleet order age, every bot's current entry gate, verdict (`STALE` = host probably down). Exit 2 when stale. |
| `./doctor.sh` | on the host (Pi) | PASS/FAIL checklist: cron lines, cron daemon, heartbeat ages, tick errors, Alpaca auth, git sync, claude CLI, Signal gateway. |
| `./install_cron.sh` | on the host | installs/re-installs the COMPLETE crontab (7 bots, operator, tracker, Signal summary, watchdog) idempotently. `--dry-run` / `--remove`. |
| `heartbeat.sh` | sourced by the run scripts | stamps `heartbeat/<bot>` after every tick and pings `HEALTHCHECK_URL` if set. |

**Set the external dead-man alert (2 minutes, free):** create a check at
https://healthchecks.io (period 15 min, grace 60 min), then on the host add
`HEALTHCHECK_URL=https://hc-ping.com/<uuid>` to `.env`. Every tick pings it; if the
Pi loses power, network, cron, or the ticks start crashing, healthchecks emails/pushes
you within the hour. Nothing on the Pi itself can alert you that the Pi is dead.

**Recovery runbook:** `./doctor.sh` -> fix FAILs top to bottom -> `./install_cron.sh`
-> `python3 fleet_status.py` should move from `STALE` to `WATCH`/`OK` on the next
open-market tick.

## Runtime model

- **Scheduled cloud agent** (chosen): a cron routine runs `bot.py` + `review.py` every
  ~15 min during market hours, and under full autonomy may edit the strategy and push.
  See `OPERATOR.md`. Requires the Alpaca keys present in the run environment.
- **Local** fallback: `python3 bot.py --loop 120` while this Mac is on.

Runtime artifacts (`state.json`, `journal.jsonl`, `equity.csv`) are gitignored.
