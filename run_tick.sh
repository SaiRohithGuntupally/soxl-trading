#!/usr/bin/env bash
# Stage 1 — one trading tick on the Pi. Cron this on weekdays; bot.py self-gates
# on the real US market clock, so ticks outside market hours just no-op.
cd "$(dirname "$0")" || exit 1
# Pick up any strategy edits the operator pushed.
git pull --rebase --quiet origin main 2>/dev/null || true
python3 bot.py >> cron.log 2>&1
