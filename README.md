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
```

### 3) Alpaca live mode
Set:

```env
BROKER_MODE=live
ALPACA_KEY_ID=...
ALPACA_SECRET_KEY=...
```

## Quick start

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
