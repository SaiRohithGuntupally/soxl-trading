#!/usr/bin/env bash
# Dead-man's switch for the fleet. Source this, then call:  heartbeat <name> <exit_code>
#   - always: touches heartbeat/<name> with an ISO timestamp (local, gitignored),
#     so doctor.sh / fleet_status.py can see when each job last ran.
#   - if HEALTHCHECK_URL is set in .env: pings it (…/fail on a non-zero exit).
#     Use a free https://healthchecks.io check with period 15m / grace 60m —
#     it emails/pushes you when the Pi stops pinging. This is the alert that
#     would have caught the Jul 8 -> Oct 6 2026 silent outage.
# Best-effort: never fails the caller, never blocks for more than ~10s.
heartbeat() {
  local name="${1:-tick}" rc="${2:-0}" dir url
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  mkdir -p "$dir/heartbeat" 2>/dev/null
  printf '%s rc=%s\n' "$(date -u +%FT%TZ)" "$rc" > "$dir/heartbeat/$name" 2>/dev/null
  url="$(grep -E '^HEALTHCHECK_URL=' "$dir/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d "\"'")"
  [ -n "$url" ] || return 0
  [ "$rc" = "0" ] || url="${url%/}/fail"
  curl -fsS -m 10 --retry 2 -o /dev/null "$url" 2>/dev/null || true
}
