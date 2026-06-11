# SOXL Trade-Entry Checklist

> Not investment advice. A mechanical filter to keep you out of SOXL when conditions
> are wrong — which is most of the time. **Cash is a position.**

Trade only when **every box in Section A is checked.** One unchecked box = no trade.

---

## A. Pre-entry gate (ALL must be true)

- [ ] **Signal from the underlying, not SOXL.** I'm reading the chart of SOXX or SMH
      (leverage distorts the SOXL chart).
- [ ] **Clean trend exists.** Underlying is above a *rising* 20-day EMA for a long
      (or below a *falling* 20-day EMA for a SOXS short). EMA is sloped, not flat.
- [ ] **Not chop.** Price is not whipsawing back and forth across the EMA over the
      last several sessions.
- [ ] **RSI(14) confirms, not exhausted.** Aligned with the trend and not pinned at a
      blow-off extreme.
- [ ] **No major event inside my hold window.** No CPI, FOMC, or **Nvidia earnings**
      in the next 1–5 trading days. (If there is → stand aside or close before it.)
- [ ] **Size is computed, not guessed.** Ran `position_size.py`; risk ≤ 2% of equity.
- [ ] **Stops are written down before entry** (initial + trail), ATR-based.
- [ ] **A ~15% overnight gap against me is survivable** at this size.

## B. In the trade

- [ ] Initial stop placed immediately (~1.5x ATR or ~5% below entry).
- [ ] Trail the stop by ~1x ATR / ~3% as the trade moves my way. Never *tighten* to
      "feel safe" — that just guarantees a whipsaw-out.
- [ ] **Days, not weeks.** Target hold 1–5 days. If no clean trend after 2–3 days →
      exit to cash regardless of P&L.

## C. Exit triggers (any ONE = out)

- [ ] Stop hit.
- [ ] Trend filter breaks (close back across the 20-day EMA).
- [ ] Held 5 days with no follow-through.
- [ ] A major event (CPI/FOMC/NVDA) is now inside my window.
- [ ] I'm about to size up after a win, or revenge-trade after a loss → close, walk away.

---

## Standing rules (read before every session)

1. **The goal is good trades + capital preservation. 10%/month is an *output*, never a
   quota.** A quota makes you force trades exactly when you shouldn't.
2. **Zero trades in a bad month is a valid, winning month.**
3. **Volatility decay is the structural enemy.** SOXL bleeds in flat/choppy tape even
   when the index goes nowhere. Your edge is mostly in *not being in it*.
4. **Fixed-fractional sizing, written before each trade.** The 2% rule is boring on
   purpose.
5. **Don't hold SOXL + SOXS together "to profit either way."** Decay gets you twice.

## Current regime note (June 2026)

High-volatility, no-confirmed-trend tape ahead of a binary catalyst (Nvidia earnings)
— the **lowest-quality** environment for this instrument. Default action is **wait in
cash** until a clean trend confirms after the dust settles.
