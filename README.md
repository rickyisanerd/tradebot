# TradeBot MCP

A small Python trading bot project that is built to work out of the box in **demo mode**, and then switch to **Alpaca paper** or **Alpaca live** by changing environment variables.

## What it does

- scans a low-priced stock universe and filters for names under your price cap
- scores each symbol with three local MCP analyzers (embedded by default, or subprocess servers if `ANALYZER_MODE=subprocess`):
  - momentum
  - reversion / pullback
  - risk
- tracks what wins and loses in SQLite and adjusts analyzer weights over time
- stores scan history, trades, open position metadata, and learning weights
- provides a FastAPI dashboard showing:
  - current picks
  - open positions
  - recent buys and sells
  - learning weights

## Modes

### 1) Demo mode (default)
No API keys required. Uses a deterministic market simulator so you can run and test the full product immediately.

### 2) Alpaca paper mode
Set:

```env
BROKER_MODE=paper
ALPACA_KEY_ID=...
ALPACA_SECRET_KEY=...
STOP_LOSS=5
USE_BROKER_PROTECTIVE_ORDERS=true
MIN_HOLD_DAYS=1
MAX_TOTAL_CAPITAL=5000
MAX_OPEN_POSITIONS=5
```

### 3) Alpaca live mode
Set:

```env
BROKER_MODE=live
ALPACA_KEY_ID=...
ALPACA_SECRET_KEY=...
STOP_LOSS=5
USE_BROKER_PROTECTIVE_ORDERS=true
MIN_HOLD_DAYS=1
MAX_TOTAL_CAPITAL=5000
MAX_OPEN_POSITIONS=5
```

## Quick start

Copy `.env.example` to `.env` and edit your local values there. Keep `.env` out of Git; commit only template changes to `.env.example`.

### Linux / macOS

```bash
./create_venv.sh
source .venv/bin/activate
python -m tradebot.cli scan
python -m tradebot.cli trade-once
python -m tradebot.cli dashboard
```

### Windows

```bat
create_venv.bat
.venv\Scripts\activate
python -m tradebot.cli scan
python -m tradebot.cli trade-once
python -m tradebot.cli dashboard
```

Open:

```text
http://127.0.0.1:8008
```

## Environment variables

The app reads its active settings from `tradebot/config.py`. Use `.env.example` as the shareable template and `.env` for machine-local secrets such as Alpaca credentials.

If you add a new key to `.env`, it will only affect runtime after the setting is also wired into `tradebot/config.py` and the code that uses it.

`STOP_LOSS` (or `STOP_LOSS_PCT`) now controls the stop distance used when the bot computes exits. In Alpaca paper/live mode, `USE_BROKER_PROTECTIVE_ORDERS=true` submits bracket entry orders so Alpaca can hold the stop-loss and take-profit legs at the broker.

`MIN_HOLD_DAYS` prevents routine target exits from firing too early, while `MAX_HOLD_DAYS` can enforce a time stop when set above `0`. `MAX_TOTAL_CAPITAL` and `MAX_OPEN_POSITIONS` cap how much exposure the bot is allowed to carry across the portfolio.

## Git helper script

Use the included script to stage, commit, and push in one step.

### Windows

```bat
push_git.bat "your commit message"
```

### Linux / macOS

```bash
./push_git.sh "your commit message"
```

Optional arguments:

- second argument: branch name, defaults to the current branch
- third argument: remote name, defaults to `origin`

## Notes

- `trade-once` advances the demo market one step, checks exits, scans again, and buys fresh candidates.
- the learning loop is intentionally simple and transparent rather than pretending to be magic wizard dust.
- this is a starter system, not financial advice, and not a promise of profits.

## Project layout

```text
tradebot/
  analytics.py
  cli.py
  config.py
  dashboard.py
  db.py
  engine.py
  models.py
  providers.py
  universe.py
  templates/index.html
tests/
```
