#!/usr/bin/env bash
# SOXL DCA accumulator tick (separate project, separate paper account).
cd "$(dirname "$0")" || exit 1
git -C .. pull --rebase --quiet origin main 2>/dev/null || true
python3 dca_bot.py >> cron.log 2>&1
