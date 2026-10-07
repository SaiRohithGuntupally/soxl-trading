#!/usr/bin/env bash
# One v2 tick. Cron: */15 * * * 1-5  (the engine decides when the decision window is).
cd "$(dirname "$0")" || exit 1
git -C .. pull --rebase --quiet origin main 2>/dev/null || true
exec python3 fleet.py tick >> fleet.log 2>&1
