#!/usr/bin/env bash
# Run ON THE HOST (the Pi) when the fleet looks dead. Read-only. Prints PASS/FAIL per check.
cd "$(dirname "$0")" || exit 1
ok(){ printf 'PASS  %s\n' "$*"; } ; bad(){ printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS+1)); }
FAILS=0
echo "== host =="; echo "$(hostname)  up: $(uptime -p 2>/dev/null || uptime)  tz: $(date +%Z)  now: $(date)"
df -h . | tail -1 | awk '{print "disk: "$4" free ("$5" used)"}'
echo "== python =="; python3 --version 2>&1
python3 -c "import bot, broker, notify, portfolio" 2>/dev/null && ok "imports" || bad "python imports (see: python3 -c 'import bot')"
echo "== git =="; git fetch -q origin 2>/dev/null && ok "fetch origin" || bad "git fetch (network/DNS/PAT?)"
behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?"); [ "$behind" = "0" ] && ok "up to date with origin" || bad "$behind commits behind origin (run_tick.sh pulls each tick — if this stays >0, the pull is failing)"
git status --porcelain | grep -q . && echo "      (dirty tree: $(git status --porcelain | wc -l) files)"
echo "== cron =="; n=$(crontab -l 2>/dev/null | grep -c 'soxl-trading'); [ "$n" -ge 7 ] && ok "$n fleet cron lines" || bad "only $n fleet cron lines (run ./install_cron.sh)"
systemctl is-active cron >/dev/null 2>&1 && ok "cron daemon active" || { systemctl is-active cronie >/dev/null 2>&1 && ok "cronie active" || bad "cron daemon not active (sudo systemctl enable --now cron)"; }
echo "== heartbeats =="
if ls heartbeat/* >/dev/null 2>&1; then
  for f in heartbeat/*; do ts=$(cut -d' ' -f1 "$f"); age=$(( ( $(date +%s) - $(date -d "$ts" +%s 2>/dev/null || echo 0) ) / 60 )); printf '      %-9s %6d min ago  %s\n' "$(basename "$f")" "$age" "$(cut -d' ' -f2 "$f")"; done
  newest=$(ls -t heartbeat/* | head -1); age=$(( ( $(date +%s) - $(date -r "$newest" +%s) ) / 60 ))
  [ "$age" -le 60 ] && ok "newest heartbeat ${age} min ago" || bad "newest heartbeat ${age} min ago (crons not firing?)"
else echo "      none yet (stamps appear after the first tick with the new run scripts)"; fi
echo "== logs =="
for l in cron.log bots/*/cron.log; do [ -f "$l" ] || continue; printf '      %-22s last: %s\n' "$l" "$(tail -1 "$l" | cut -c1-90)"; done
errs=$(grep -h -i 'tick failed\|Traceback\|HTTP 40[13]\|HTTP 5' cron.log bots/*/cron.log 2>/dev/null | tail -5)
[ -n "$errs" ] && { echo "      recent errors:"; echo "$errs" | sed 's/^/        /'; } || ok "no recent tick errors in logs"
echo "== alpaca =="; python3 - <<'PY' && ok "alpaca auth + clock" || bad "alpaca API (keys in .env revoked? network?)"
import broker; k,s=broker.load_creds(); a=broker.get_account(k,s); c=broker.get_clock(k,s)
print(f"      account {a['status']} equity ${float(a['equity']):,.0f}  market_open={c['is_open']}  api_time={c['timestamp'][:19]}")
PY
grep -q '^HEALTHCHECK_URL=.' .env 2>/dev/null && ok "HEALTHCHECK_URL set (external dead-man alert on)" || bad "HEALTHCHECK_URL not set in .env — no one is alerted if this host dies (see README: Operations)"
echo "== operator =="; command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] && ok "claude CLI present ($($HOME/.local/bin/claude --version 2>/dev/null || claude --version 2>/dev/null))" || bad "claude CLI missing (operator.sh degrades to review-only)"
echo "== signal =="; systemctl --user is-active openclaw-gateway.service >/dev/null 2>&1 && ok "openclaw gateway active" || echo "      openclaw gateway not active (alerts off; notify is best-effort)"
echo; [ "$FAILS" = 0 ] && echo "ALL CHECKS PASSED — if still no trades, run: python3 fleet_status.py" || echo "$FAILS CHECK(S) FAILED — fix top to bottom, then: python3 fleet_status.py"
exit $FAILS
