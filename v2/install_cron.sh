#!/usr/bin/env bash
# Install the v2 crontab on this host: removes EVERY v1 "# soxl-trading" line (the two
# fleets trade the same symbols on the same account and must never run together) and
# installs the single v2 tick plus a daily status/watchdog line.
#   ./install_cron.sh            # install
#   ./install_cron.sh --dry-run  # show
#   ./install_cron.sh --remove   # remove v2 lines only
set -e
cd "$(dirname "$0")" || exit 1
R="$(pwd)"; TAG="# soxl-v2"; V1TAG="# soxl-trading"
gen() {
  echo "$TAG fleet v2 installed $(date +%F); one process, decisions in the last 20 min of the session"
  echo "*/15 * * * 1-5 $R/run.sh $TAG tick"
  echo "35 14 * * 1-5 cd $R && python3 fleet.py status >> status.log 2>&1 $TAG daily status"
}
keep="$(crontab -l 2>/dev/null | grep -v "$TAG" | grep -v "$V1TAG" || true)"
case "${1:-}" in
  --dry-run) gen; echo; echo "(would also remove $(crontab -l 2>/dev/null | grep -c "$V1TAG" || true) v1 lines)"; exit 0 ;;
  --remove)  { crontab -l 2>/dev/null | grep -v "$TAG" || true; } | crontab - ; echo "v2 lines removed"; exit 0 ;;
esac
{ [ -n "$keep" ] && printf '%s\n' "$keep"; gen; } | crontab -
echo "installed:"; crontab -l | grep "$TAG"
echo "v1 lines remaining: $(crontab -l | grep -c "$V1TAG" || true)"
