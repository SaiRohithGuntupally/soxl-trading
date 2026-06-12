#!/usr/bin/env bash
# Stage 2 — autonomous operator for the MULTI-BOT system. Reviews every bot
# (root SOXL + bots/<TICKER>/) and, if `claude` is installed, lets it tune any
# bot per OPERATOR.md, then commit & push. Trading ticks are handled by the
# dedicated run_tick.sh / run_bot.sh crons; this is the review/edit cycle.
cd "$(dirname "$0")" || exit 1
ts="$(date -u +%FT%TZ)"
export PATH="$HOME/.local/bin:$PATH"
[ -f "$HOME/soxl-trading/.operator-env" ] && . "$HOME/soxl-trading/.operator-env"

git pull --rebase --quiet origin main 2>/dev/null || true

# gather a review for every bot
: > all_reviews.json
for cfg in config.json bots/*/config.json; do
  [ -f "$cfg" ] || continue
  echo "=== bot config: $cfg ===" >> all_reviews.json
  python3 review.py --config "$cfg" --json >> all_reviews.json 2>> cron.log || true
done

if command -v claude >/dev/null 2>&1; then
  PROMPT="You are the autonomous operator for a MULTI-BOT paper trading system on a \
Raspberry Pi (repo $(pwd)). Read OPERATOR.md and follow it EXACTLY, for EACH bot. \
Per-bot reviews are below; each bot is the root config.json (SOXL) or a \
bots/<TICKER>/config.json. For each, decide change-or-nothing; make AT MOST one \
change total this run, and only if a *structural* problem is evidenced in the data \
(do NOT tune in a drawdown or just because you ran). Hard rules you must never break: \
never set max_daily_loss_pct above 10; never raise SOXL risk_pct above 4 or a NEW \
stock's risk_pct above 2 (until forward results prove it); never trade or flatten \
anything but each bot's own symbol; never weaken the portfolio breaker; never commit \
.env/secrets; validate any strategy change with backtest.py / analyze.py first. If \
you change a config/code: commit, push, and append a dated line to NOTES.md naming \
the bot, the evidence, and the change.

REVIEWS:
$(cat all_reviews.json)"
  claude -p "$PROMPT" --dangerously-skip-permissions >> operator.log 2>&1 || true
fi

git add -A >/dev/null 2>&1 || true
git commit -q -m "operator: $ts" >/dev/null 2>&1 || true
git push -q origin main >/dev/null 2>&1 || true
