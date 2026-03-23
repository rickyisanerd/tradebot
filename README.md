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
- can cache recent congressional PTR trades from official report PDFs and filter them under your price cap
- can cache recent SEC filing signals for the active scan universe using the official SEC submissions feed
- provides a FastAPI dashboard showing:
  - current picks
  - recent congressional buys and sells under your congress price cap
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
python run_tests.py
python -m tradebot.cli scan
python -m tradebot.cli trade-once
python -m tradebot.cli refresh-signals
python -m tradebot.cli refresh-sec
python -m tradebot.cli refresh-earnings
python -m tradebot.cli refresh-macro
python -m tradebot.cli dashboard
```

### Windows

```bat
create_venv.bat
.venv\Scripts\activate
python run_tests.py
python -m tradebot.cli scan
python -m tradebot.cli trade-once
python -m tradebot.cli refresh-signals
python -m tradebot.cli refresh-sec
python -m tradebot.cli refresh-earnings
python -m tradebot.cli refresh-macro
python -m tradebot.cli dashboard
```

Open:

```text
http://127.0.0.1:8008
```

## Railway deploy

This repo now includes [Procfile](c:/Users/ricky/OneDrive/Documents/tradebot_mcp_bot/tradebot_mcp_bot/Procfile) and [railway.json](c:/Users/ricky/OneDrive/Documents/tradebot_mcp_bot/tradebot_mcp_bot/railway.json) so Railway can deploy it directly from GitHub.

Recommended Railway setup:

- start command: `python -m uvicorn main:app --host 0.0.0.0 --port $PORT`
- health check path: `/health`
- public port: Railway injects `PORT` automatically, and the app now uses it by default
- if you want SQLite state to survive redeploys, mount a Railway volume and set `DATA_DIR=/data` (or your chosen mounted path)

If you do not mount a volume, local files like the SQLite database and demo broker state will be ephemeral on Railway.

Recommended Railway operating model:

- Run exactly one instance. This app uses SQLite plus in-process scheduling, so it is not designed for multiple replicas.
- Mount a persistent volume and set `DATA_DIR=/data`.
- Start with `AUTO_TRADE_ENABLED=false` and use the dashboard or `trade-once` manually first.
- Keep live risk tight: `MAX_TOTAL_CAPITAL=1000`, `MAX_OPEN_POSITIONS=1`, `MAX_NEW_POSITIONS_PER_RUN=1`, `RISK_PER_TRADE_PCT=0.005`, `MAX_POSITION_PCT=0.10`, `STOP_LOSS_PCT=0.05`.

### Railway paper profile

```env
BROKER_MODE=paper
ALPACA_KEY_ID=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
DATA_DIR=/data
AUTO_TRADE_ENABLED=false
MAX_TOTAL_CAPITAL=1000
MAX_OPEN_POSITIONS=1
MAX_NEW_POSITIONS_PER_RUN=1
MAX_POSITION_PCT=0.10
RISK_PER_TRADE_PCT=0.005
STOP_LOSS_PCT=0.05
USE_BROKER_PROTECTIVE_ORDERS=true
MIN_HOLD_DAYS=1
MAX_HOLD_DAYS=5
SEC_USER_AGENT=TradeBot MCP your-email@example.com
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key
```

### Railway cautious live profile

```env
BROKER_MODE=live
ALPACA_KEY_ID=your_live_key
ALPACA_SECRET_KEY=your_live_secret
DATA_DIR=/data
AUTO_TRADE_ENABLED=false
MAX_TOTAL_CAPITAL=1000
MAX_OPEN_POSITIONS=1
MAX_NEW_POSITIONS_PER_RUN=1
MAX_POSITION_PCT=0.10
RISK_PER_TRADE_PCT=0.005
STOP_LOSS_PCT=0.05
USE_BROKER_PROTECTIVE_ORDERS=true
MIN_HOLD_DAYS=1
MAX_HOLD_DAYS=5
SEC_USER_AGENT=TradeBot MCP your-email@example.com
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key
```

## Environment variables

The app reads its active settings from `tradebot/config.py`. Use `.env.example` as the shareable template and `.env` for machine-local secrets such as Alpaca credentials.

If you add a new key to `.env`, it will only affect runtime after the setting is also wired into `tradebot/config.py` and the code that uses it.

`STOP_LOSS` (or `STOP_LOSS_PCT`) now controls the stop distance used when the bot computes exits. In Alpaca paper/live mode, `USE_BROKER_PROTECTIVE_ORDERS=true` submits bracket entry orders so Alpaca can hold the stop-loss and take-profit legs at the broker.

`MIN_HOLD_DAYS` prevents routine target exits from firing too early, while `MAX_HOLD_DAYS` can enforce a time stop when set above `0`. `MAX_TOTAL_CAPITAL` and `MAX_OPEN_POSITIONS` cap how much exposure the bot is allowed to carry across the portfolio.

`CONGRESS_REPORT_URLS` accepts a comma-separated list of official House or Senate PTR PDF URLs. Run `python -m tradebot.cli refresh-congress` or click `Refresh Congress` in the dashboard to cache those trades locally, price them through the active broker feed, and show only names trading at or below `CONGRESS_MAX_PRICE`.

`SEC_USER_AGENT` should be set to a descriptive contact string before using `python -m tradebot.cli refresh-sec`. The bot uses the official SEC submissions feed to cache recent forms such as `4`, `8-K`, `10-Q`, `10-K`, and common offering-related forms, then folds those cached signals into the decision-support analyzer.

`ALPHA_VANTAGE_API_KEY` enables `python -m tradebot.cli refresh-earnings`, which caches near-term earnings dates from the Alpha Vantage earnings calendar so the decision-support analyzer can penalize setups that are about to run into earnings gap risk.

`python -m tradebot.cli refresh-macro` caches upcoming CPI and FOMC dates from official BLS and Federal Reserve calendar pages so the decision-support analyzer can treat market-wide event risk as a first-class input.

`python -m tradebot.cli trade-once` now trades strictly from cached signal data. Use `python -m tradebot.cli refresh-signals` first when you want to update every external feed in one pass. The dashboard also exposes source health so stale or failed feeds are visible instead of silently degrading decisions.

You can tune reliability and influence with env vars such as `CONGRESS_FRESHNESS_HOURS`, `SEC_FRESHNESS_HOURS`, `EARNINGS_FRESHNESS_HOURS`, `MACRO_FRESHNESS_HOURS`, and `DECISION_SUPPORT_*_WEIGHT`.

Stale, failed, disabled, or zero-weight external sources are now excluded from candidate scoring instead of being treated like fresh signals, and the dashboard shows each candidate’s per-source signal usage state.

You can also require a minimum number of cached records before a source is trusted by setting `CONGRESS_MIN_RECORDS`, `SEC_MIN_RECORDS`, `EARNINGS_MIN_RECORDS`, and `MACRO_MIN_RECORDS`. Sources that are fresh but too thin are marked `low-confidence` and excluded from scoring.

Each source also supports retry backoff controls with `CONGRESS_RETRY_MINUTES`, `SEC_RETRY_MINUTES`, `EARNINGS_RETRY_MINUTES`, and `MACRO_RETRY_MINUTES`. Repeated failures now enter a visible `backoff` state so the bot stops retrying the same broken feed every cycle.

The dashboard now also shows a recent signal refresh history so you can see whether a source has been succeeding, failing, disabled, or sitting in backoff over time.

If you need an operational escape hatch, each source also supports an override mode: `auto`, `disabled`, `ignore-backoff`, or `trusted`. Use `*_OVERRIDE_MODE` env vars carefully when debugging or working around flaky upstreams.

## Running tests

Use `python run_tests.py` as the supported test command.

Convenience wrappers are included:

```bash
./run_tests.sh
```

```bat
run_tests.bat
```

The repo still contains pytest-style test functions, but `pytest` itself can fail on some Windows/OneDrive setups because its temp-directory cleanup hits filesystem permission issues. `run_tests.py` runs the same test module directly and avoids that runner-specific failure mode.

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
