#!/usr/bin/env bash
# Generic multi-bot tick: run_bot.sh <path/to/config.json>
# Pulls latest strategy, runs one tick for that bot, logs next to its config.
cd "$(dirname "$0")" || exit 1
CFG="${1:?usage: run_bot.sh <config.json>}"
git pull --rebase --quiet origin main 2>/dev/null || true
python3 bot.py --config "$CFG" >> "$(dirname "$CFG")/cron.log" 2>&1
