#!/usr/bin/env bash
# Stage 2 — full autonomous cycle on the Pi: trade, review, and (if the `claude`
# CLI is installed + authenticated) let Claude autonomously edit the strategy per
# OPERATOR.md, then commit & push. Safe to cron even without claude installed:
# it will just trade + review and skip the edit step.
cd "$(dirname "$0")" || exit 1
ts="$(date -u +%FT%TZ)"

# cron runs with a minimal PATH — make sure the claude CLI (~/.local/bin) and the
# Anthropic key (from ~/.config) are available.
export PATH="$HOME/.local/bin:$PATH"
[ -f "$HOME/soxl-trading/.operator-env" ] && . "$HOME/soxl-trading/.operator-env"

git pull --rebase --quiet origin main 2>/dev/null || true   # latest strategy
python3 bot.py        >> cron.log 2>&1 || true              # one trading tick
python3 review.py --json > last_review.json 2>> cron.log || true

if command -v claude >/dev/null 2>&1; then
  PROMPT="You are the autonomous SOXL operator running on a Raspberry Pi in the \
repo at $(pwd). Read OPERATOR.md and follow it EXACTLY. The latest review JSON is \
below. Make AT MOST one change, and only if a *structural* problem is evidenced in \
the data — otherwise do nothing (do not edit just because you ran or are in a \
drawdown). Hard rules you must never break: never set max_daily_loss_pct above 10, \
never raise risk_pct above 2.0, never trade or flatten anything but SOXL, never \
commit .env or secrets. If you do change code/config: commit with a message stating \
the diagnosis->fix, append a dated line to NOTES.md, and push. \

REVIEW JSON:
$(cat last_review.json)"
  claude -p "$PROMPT" --dangerously-skip-permissions >> operator.log 2>&1 || true
fi

# Commit any strategy/NOTES changes (journal stays local + gitignored). Empty
# commits are skipped by the || true.
git add -A >/dev/null 2>&1 || true
git commit -q -m "operator: $ts" >/dev/null 2>&1 || true
git push -q origin main >/dev/null 2>&1 || true
