#!/usr/bin/env bash
# Install (or re-install) the COMPLETE fleet crontab on this host in one command.
# Idempotent: replaces every existing soxl-trading line, keeps your other crons.
#   ./install_cron.sh            # install
#   ./install_cron.sh --dry-run  # print what would be installed
#   ./install_cron.sh --remove   # strip all fleet crons (e.g. before moving hosts)
# Times are the HOST's local clock; the Pi is America/Denver (market 7:30-14:00 MT).
# Minute offsets keep the 7 bots' git pulls / ledger writes from colliding.
set -e
cd "$(dirname "$0")" || exit 1
R="$(pwd)"
TAG="# soxl-trading"
gen() {
  echo "$TAG fleet — installed $(date +%F) by install_cron.sh; edit there, not here"
  echo "*/15 * * * 1-5 $R/run_tick.sh $TAG SOXL"
  local m=3
  for b in MSTR PLTR TNA UPRO; do           # trend bots: 3,18,33,48 / 6.. / 9.. / 12..
    echo "$m,$((m+15)),$((m+30)),$((m+45)) * * * 1-5 $R/run_bot.sh $R/bots/$b/config.json $TAG $b"
    m=$((m+3))
  done
  echo "1,16,31,46 * * * 1-5 $R/run_bot.sh $R/bots/TQQQ/config.json $TAG TQQQ"
  echo "4,19,34,49 * * * 1-5 $R/run_bot.sh $R/bots/LABU/config.json $TAG LABU"
  echo "7 8,10,12,14 * * 1-5 $R/operator.sh $TAG operator (Claude review, 4x/day)"
  echo "5 14 * * 1-5 cd $R && python3 tracker.py --snapshot >> tracker.log 2>&1 $TAG forward tracker daily"
  echo "15 14 * * 1-5 cd $R && python3 notify.py --summary >> notify.log 2>&1 $TAG Signal daily summary"
  echo "20 14 * * 5 cd $R && python3 tracker.py --send >> tracker.log 2>&1 $TAG forward tracker weekly"
  echo "30 14 * * 1-5 cd $R && python3 fleet_status.py --notify >> fleet_status.log 2>&1 $TAG stale watchdog (Signal)"
}
for b in MSTR PLTR TNA UPRO TQQQ LABU; do [ -f "bots/$b/config.json" ] || { echo "missing bots/$b/config.json"; exit 1; }; done
existing="$(crontab -l 2>/dev/null | grep -v "$TAG" || true)"
case "${1:-}" in
  --dry-run) gen; exit 0 ;;
  --remove)  printf '%s\n' "$existing" | crontab - ; echo "fleet crons removed"; exit 0 ;;
esac
{ [ -n "$existing" ] && printf '%s\n' "$existing"; gen; } | crontab -
echo "installed $(crontab -l | grep -c "$TAG") fleet cron lines:"; crontab -l | grep "$TAG"
