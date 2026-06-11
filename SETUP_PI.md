# Running the SOXL bot on a Raspberry Pi (always-on host)

Two stages. Stage 1 gets it **trading continuously** (no Claude needed). Stage 2
adds the **autonomous Claude review/edit** loop. Do Stage 1 first and confirm it
trades before adding Stage 2.

All commands run on the **Pi** (via Raspberry Pi Connect shell, SSH, or its desktop
terminal). Assumes 64-bit Raspberry Pi OS (Debian-based).

---

## Stage 1 — the always-on trader

### 1. Prerequisites
```bash
sudo apt update && sudo apt install -y git python3
python3 --version          # need 3.8+ (Pi OS ships 3.9+/3.11)
```

### 2. Clone the repo
```bash
git clone https://github.com/SaiRohithGuntupally/soxl-trading.git ~/soxl-trading
cd ~/soxl-trading
```

### 3. Add your Alpaca PAPER keys (stays on the Pi, never committed)
```bash
cat > ~/soxl-trading/.env <<'EOF'
APCA_API_KEY_ID=YOUR_PAPER_KEY_ID
APCA_API_SECRET_KEY=YOUR_PAPER_SECRET
EOF
chmod 600 ~/soxl-trading/.env
```

### 4. Smoke-test (Pi uses system CA certs, so SSL "just works" — no macOS certifi step)
```bash
python3 bot.py --status      # should print account equity + positions
python3 bot.py --dry-run     # one tick, decides but places NO order
```
If `--status` prints your equity, the Pi can reach Alpaca and the keys are good.

### 5. Let it place real paper trades
```bash
python3 bot.py               # one live tick (paper). Re-run to see it act.
```

### 6. Schedule it (cron, every 15 min on weekdays)
```bash
crontab -e
```
Add this line (the bot self-gates on the real market clock, so off-hours ticks
just no-op — no timezone math needed):
```
*/15 * * * 1-5 /home/<USER>/soxl-trading/run_tick.sh
```
Replace `<USER>` with your Pi username (run `whoami`). Check it's logging:
```bash
tail -f ~/soxl-trading/cron.log
```

**You now have a continuous paper trader.** Stop anytime: `crontab -e` and remove the
line, or `python3 bot.py --flatten` to close the SOXL position.

---

## Stage 2 — autonomous Claude review + self-edits (optional)

This lets Claude diagnose underperformance and edit the strategy on its own, per
`OPERATOR.md`, committing every change. Needs the `claude` CLI on the Pi.

### A. Git push auth (so the Pi can push its edits)
Use a fine-grained GitHub token with **Contents: Read and write** on this repo:
```bash
cd ~/soxl-trading
git config user.name  "SOXL Bot (Pi)"
git config user.email "you@example.com"
git config credential.helper store
printf 'https://SaiRohithGuntupally:YOUR_GITHUB_PAT@github.com\n' > ~/.git-credentials
chmod 600 ~/.git-credentials
git push origin main          # should succeed without prompting
```

### B. Install + authenticate Claude Code (64-bit Pi)
```bash
curl -fsSL https://claude.ai/install.sh | bash    # or: npm i -g @anthropic-ai/claude-code
claude --version
# Authenticate headless with an API key (cron has no browser):
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc && source ~/.bashrc
```
> Note: this consumes Anthropic API credits each run. Keep the cadence modest.

### C. Swap the cron to the full operator (slower cadence — edits should be rare)
```bash
crontab -e
```
Replace the Stage-1 line with:
```
*/30 13-21 * * 1-5 /home/<USER>/soxl-trading/operator.sh
```
(≈ every 30 min, 13:00–21:00 UTC = 9:00–17:00 ET. `operator.sh` still runs `bot.py`
each time, so this both trades and reviews.)

Watch it:
```bash
tail -f ~/soxl-trading/cron.log ~/soxl-trading/operator.log
git log --oneline             # see the bot's own commits appear
```

---

## Safety recap (enforced in code, not just docs)
- Kill switch hard-capped at **10%** daily SOXL loss — the operator cannot weaken it.
- Bot **only ever trades/flattens SOXL**; your other paper positions are untouched.
- `.env` is gitignored; never commit keys (the repo is **public**).
- Every self-edit is a git commit → revert any bad one with `git revert <sha>`.
