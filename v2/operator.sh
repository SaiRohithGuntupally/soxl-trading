#!/usr/bin/env bash
# Weekly evidence gatherer (launchd: Fri 14:40 MT). Writes review.json and a report stub.
# Places no orders, edits no code. The review is done by Claude in a session (OPERATOR.md).
cd "$(dirname "$0")" || exit 1
export PATH="/Library/Frameworks/Python.framework/Versions/3.9/bin:/usr/local/bin:/usr/bin:/bin"
ts="$(date +%F)"
mkdir -p reports
{
  echo "=== status ==="; python3 fleet.py status --json 2>&1
  echo "=== ledger: last 8 days ==="; python3 - <<'PY' 2>&1
import sqlite3, datetime as dt
c = sqlite3.connect("fleet.db")
since = (dt.date.today() - dt.timedelta(days=8)).isoformat()
print("equity_daily:", c.execute("select et_date, round(equity), round(core_mv), round(bots_mv) from equity_daily where et_date>=? order by et_date", (since,)).fetchall())
print("decisions:", c.execute("select ts, symbol, action, substr(detail,1,120) from decisions where et_date>=? order by id", (since,)).fetchall())
print("orders:", c.execute("select ts, symbol, side, qty, kind, order_id from orders where ts>=? order by id", (since,)).fetchall())
print("tick modes:", c.execute("select mode, count(*) from ticks where et_date>=? group by mode", (since,)).fetchall())
print("errors:", c.execute("select ts, substr(detail,1,300) from ticks where et_date>=? and (detail like '%error%' or detail like '%REFUSED%') order by id desc limit 10", (since,)).fetchall())
PY
  echo "=== fleet.log tail ==="; tail -40 fleet.log 2>/dev/null
  echo "=== tests ==="; python3 -m unittest discover -s tests 2>&1 | tail -5
  echo "=== backtest ==="; python3 fleet.py backtest 2>&1 | tail -30
} > review.json
[ -f "reports/$ts.md" ] || printf '# Weekly fleet review %s\n\nEvidence gathered in v2/review.json. Review pending (OPERATOR.md).\n' "$ts" > "reports/$ts.md"
echo "$(date -u +%FT%TZ) evidence gathered -> review.json, reports/$ts.md" >> operator.log
