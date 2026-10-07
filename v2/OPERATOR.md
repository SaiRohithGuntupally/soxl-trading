# v2 operator: the weekly improvement review

Ownership of this paper-trading fleet sits with Claude, per the owner (2026-10-06):
"take ownership, keep trading, make yourself better." Being better means fewer bugs,
less drift between what is backtested and what is live, and config changes only when
evidence says so. It does not mean more trades.

`operator.sh` runs every Friday after the close (launchd) and GATHERS the evidence into
`v2/review.json` plus a report stub in `v2/reports/`. It places no orders and edits no
code. The review itself is done by Claude in a session (ask: "run the weekly fleet
review"), following the steps below. Autonomous self-editing from a cron was not
enabled because it requires running Claude with permissions bypassed; the owner can
enable that explicitly if wanted.

## The review

1. Read `v2/README.md`, this file, the latest `v2/reports/*.md`, and section 5 of
   `research/PROFIT-REVIEW-2026-10-06.md`.
2. Study `v2/review.json`: status, ledger (equity, decisions, orders, tick modes,
   errors), fleet.log tail, test and backtest output.
3. Classify what you see into exactly one of:
   - **Bug / drift** (a tick error, a stop missing, a decision outside the window, a
     fill that does not match the plan, a P&L that does not reconcile, a failing test).
     Fix it: add a failing test in `v2/tests/` first, make it pass, keep the diff small.
   - **Evidence for a config change** (only with out-of-sample support: extend
     `research/` with a script, run it, and require the change to hold in BOTH the
     BACK-OOS and TUNE windows of `python3 v2/fleet.py backtest` and under +-20%
     perturbation). One change per week at most.
   - **Nothing structural.** A losing week with the rules followed is not a bug. Say so.
4. Validate: `python3 -m unittest discover -s v2/tests` passes; `python3 v2/fleet.py
   backtest` runs; `python3 v2/fleet.py status` reaches Alpaca.
5. Complete `v2/reports/YYYY-MM-DD.md`: equity, week P&L, decisions, what changed and
   why (with numbers), what was deliberately left alone.
6. Commit with a message starting `operator:`, ending with the Co-Authored-By line. Push.

## Hard rules (the code enforces most of them)

- Never raise `max_daily_loss_pct` above 10, `portfolio_max_loss_pct` above 15,
  `max_gross_pct` above 100, SOXL `risk_pct` above 4 or any other bot above 2.
- Never widen the decision window past 30 minutes or decide outside it. The
  partial-bar bug is the single biggest loss source found in this project.
- Never add a bot or a strategy without a walk-forward result in `research/`.
- Never move the core fraction by more than 10 points in one week, never above 70%.
- Never trade, flatten or act on symbols outside `config.json`; the owner's manual
  positions (AVGO, NVDA, SMCI, VTI, WMT, XLK) are not the fleet's.
- Never place orders from a review. Only `fleet.py tick` trades.
- Never commit `.env`, keys, `fleet.db` or logs. No em dashes in files you write.
- If anything is ambiguous, do nothing and say why in the report.
