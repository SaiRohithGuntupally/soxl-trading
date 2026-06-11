#!/usr/bin/env python3
"""
SOXL position-size & stop calculator.

Core idea: dollar risk per trade is FIXED (default 2% of equity). Position size
falls out of how wide your stop is. Wider stops (high ATR / high volatility) =>
fewer shares, so your dollar risk stays constant no matter the regime.

Usage:
    python3 position_size.py

    # or non-interactive with flags:
    python3 position_size.py --equity 25000 --entry 188 --atr 14 \
        --risk-pct 2 --stop-atr 1.5 --trail-atr 1.0

Stop logic:
    initial stop = entry - (stop_atr * ATR)
    trail stop   = price - (trail_atr * ATR)   (printed as a starting trail level)

If you'd rather use a fixed-percent stop instead of ATR, pass --stop-pct 5
(and optionally --trail-pct 3); it overrides the ATR stop.
"""

from __future__ import annotations

import argparse
import sys


def compute(equity: float, entry: float, risk_pct: float,
            atr: float | None, stop_atr: float, trail_atr: float,
            stop_pct: float | None, trail_pct: float | None) -> dict:
    if equity <= 0 or entry <= 0:
        raise ValueError("equity and entry must be positive")

    dollar_risk = equity * (risk_pct / 100.0)

    # Determine stop distance (per share) from either ATR or fixed percent.
    if stop_pct is not None:
        stop_distance = entry * (stop_pct / 100.0)
        stop_price = entry - stop_distance
        if trail_pct is not None:
            trail_price = entry - entry * (trail_pct / 100.0)
        else:
            trail_price = None
        stop_basis = f"{stop_pct:.2f}% fixed"
    else:
        if atr is None or atr <= 0:
            raise ValueError("ATR is required (or use --stop-pct for a fixed stop)")
        stop_distance = stop_atr * atr
        stop_price = entry - stop_distance
        trail_price = entry - trail_atr * atr
        stop_basis = f"{stop_atr:.2f}x ATR ({atr})"

    if stop_distance <= 0:
        raise ValueError("stop distance is zero/negative; check inputs")

    raw_shares = dollar_risk / stop_distance
    shares = int(raw_shares)  # round DOWN, never up, on a 3x product
    position_value = shares * entry
    actual_risk = shares * stop_distance
    leverage_pct = (position_value / equity) * 100 if equity else 0.0

    return {
        "dollar_risk": dollar_risk,
        "stop_distance": stop_distance,
        "stop_price": stop_price,
        "trail_price": trail_price,
        "stop_basis": stop_basis,
        "shares": shares,
        "position_value": position_value,
        "actual_risk": actual_risk,
        "exposure_pct": leverage_pct,
    }


def fnum(x: float) -> str:
    return f"{x:,.2f}"


def report(p: dict, equity: float, entry: float, risk_pct: float) -> str:
    lines = [
        "=" * 48,
        "  SOXL POSITION SIZE",
        "=" * 48,
        f"  Account equity     : ${fnum(equity)}",
        f"  Entry price        : ${fnum(entry)}",
        f"  Risk per trade     : {risk_pct:.2f}%  (= ${fnum(p['dollar_risk'])})",
        f"  Stop basis         : {p['stop_basis']}",
        "-" * 48,
        f"  >> SHARES          : {p['shares']}",
        f"  Position value     : ${fnum(p['position_value'])}",
        f"  Exposure vs equity : {p['exposure_pct']:.1f}%",
        f"  Initial STOP       : ${fnum(p['stop_price'])}  (-${fnum(p['stop_distance'])}/sh)",
    ]
    if p["trail_price"] is not None:
        lines.append(f"  Trail start        : ${fnum(p['trail_price'])}")
    lines += [
        f"  Actual $ at risk   : ${fnum(p['actual_risk'])}",
        "=" * 48,
        "  Reminder: stops fill at the OPEN on a gap, not your",
        "  stop price. Size so a ~15% overnight gap is survivable.",
        "=" * 48,
    ]
    return "\n".join(lines)


def prompt_float(label: str, default: float | None = None) -> float:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  ...enter a number.")


def interactive() -> None:
    print("SOXL position-size calculator (Ctrl-C to quit)\n")
    equity = prompt_float("Account equity ($)")
    entry = prompt_float("Entry price ($)")
    risk_pct = prompt_float("Risk per trade (%)", 2.0)
    atr = prompt_float("ATR (14) of the UNDERLYING in $ — 0 to use % stop", 0.0)
    if atr > 0:
        stop_atr = prompt_float("Stop = how many x ATR below entry", 1.5)
        trail_atr = prompt_float("Trail = how many x ATR below price", 1.0)
        p = compute(equity, entry, risk_pct, atr, stop_atr, trail_atr, None, None)
    else:
        stop_pct = prompt_float("Stop (% below entry)", 5.0)
        trail_pct = prompt_float("Trail (% below price)", 3.0)
        p = compute(equity, entry, risk_pct, None, 0, 0, stop_pct, trail_pct)
    print("\n" + report(p, equity, entry, risk_pct))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="SOXL position-size & stop calculator")
    ap.add_argument("--equity", type=float)
    ap.add_argument("--entry", type=float)
    ap.add_argument("--risk-pct", type=float, default=2.0)
    ap.add_argument("--atr", type=float)
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--trail-atr", type=float, default=1.0)
    ap.add_argument("--stop-pct", type=float, help="fixed %% stop, overrides ATR")
    ap.add_argument("--trail-pct", type=float)
    args = ap.parse_args(argv)

    if args.equity is None or args.entry is None:
        interactive()
        return 0

    p = compute(args.equity, args.entry, args.risk_pct, args.atr,
                args.stop_atr, args.trail_atr, args.stop_pct, args.trail_pct)
    print(report(p, args.equity, args.entry, args.risk_pct))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (KeyboardInterrupt, EOFError):
        print("\naborted.")
        sys.exit(130)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
