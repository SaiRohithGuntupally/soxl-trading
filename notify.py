#!/usr/bin/env python3
"""
Best-effort Signal notifications via the user's openclaw gateway.

Design rules:
- NEVER raise into the caller. Trading must not break because Signal is down.
- Phone number stays out of the (public) repo: SIGNAL_TARGET is read from env or
  the gitignored .env. No target / no openclaw binary -> silent no-op.
- Failures are logged to notify.log so we can tell if the Signal backend is down.

CLI:
  python3 notify.py "some message"     # send an ad-hoc message
  python3 notify.py --summary          # build + send a performance summary
  python3 notify.py --test             # send a wiring test
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OPENCLAW = "/home/rohith/.npm-global/bin/openclaw"


def _env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    if v:
        return v
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, val = line.partition("=")
                    if k.strip() == key:
                        return val.strip().strip('"').strip("'")
    return default


def _log(msg: str) -> None:
    try:
        with open(os.path.join(HERE, "notify.log"), "a") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


def send(msg: str) -> bool:
    """Send `msg` to Signal via openclaw. Returns True on success, never raises."""
    target = _env("SIGNAL_TARGET")
    binpath = _env("OPENCLAW_BIN", DEFAULT_OPENCLAW)
    if not target or not binpath or not os.path.exists(binpath):
        _log(f"SKIP (no target/binary) msg={msg!r}")
        return False
    try:
        subprocess.run(
            [binpath, "message", "send", "--channel", "signal",
             "-t", target, "-m", msg],
            timeout=30, check=True, capture_output=True,
        )
        return True
    except Exception as e:
        detail = getattr(e, "stderr", b"")
        detail = detail.decode(errors="replace")[:200] if isinstance(detail, bytes) else str(e)
        _log(f"FAIL {type(e).__name__}: {detail} | msg={msg!r}")
        return False


def build_summary() -> str:
    """Compose a concise performance summary from review.py + the live account."""
    import review, broker
    rows = review.load_journal()
    a = review.analyze(rows)
    lines = ["📊 SOXL bot summary"]
    try:
        key, sec = broker.load_creds()
        acct = broker.get_account(key, sec)
        pos = next((p for p in broker.get_positions(key, sec)
                    if p["symbol"] == "SOXL"), None)
        lines.append(f"Equity ${float(acct['equity']):,.0f}")
        if pos:
            lines.append(f"SOXL {pos['qty']} sh, intraday "
                         f"${float(pos['unrealized_intraday_pl']):+,.0f}")
        else:
            lines.append("SOXL: flat")
    except Exception as e:
        lines.append(f"(live account unavailable: {type(e).__name__})")
    days = a["cum_realized_by_day"]
    if days:
        last_day = sorted(days)[-1]
        lines.append(f"Today P&L ${days[last_day]:+,.0f}")
    lines.append(f"Trades: {a['opens']} open / {a['trend_closes']} exit / "
                 f"{a['kill_switches']} kill")
    lines.append(a["flags"][0])
    return "\n".join(lines)


def main(argv):
    if "--summary" in argv:
        ok = send(build_summary())
    elif "--test" in argv:
        ok = send("✅ SOXL bot — Signal notifications wired up (test)")
    else:
        msg = " ".join(argv) or "(empty)"
        ok = send(msg)
    print("sent" if ok else "NOT sent (Signal backend down or unconfigured — see notify.log)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
