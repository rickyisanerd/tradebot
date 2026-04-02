# TradeBot MCP

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new)

A Python stock trading bot that scans low-priced equities, scores them with multiple analyzers, and learns from its own wins and losses over time. Works out of the box in **demo mode** -- no API keys required -- and switches to **Alpaca paper** or **Alpaca live** trading by changing environment variables.

---

## What it does

- Scans a low-priced stock universe and filters for names under your price cap
- Scores each symbol with three local MCP analyzers (embedded by default, or subprocess servers if `ANALYZER_MODE=subprocess`):
  - **Momentum** -- trend strength and breakout detection
  - **Reversion / Pullback** -- mean-reversion setups
  - **Risk** -- volatility and downside guard
- A fourth **Decision Support** analyzer folds in external signals: congressional trades, SEC filings, earnings dates, macro events, and short-volume data
- Tracks wins and losses in SQLite and adjusts analyzer weights over time
- Caches congressional PTR trades, SEC filings, earnings calendars, and CPI/FOMC dates
- Provides a FastAPI dashboard showing picks, positions, trades, signal health, and learning weights
- Supports inverse ETF hedging when the market trends down
- Peak-based trailing stop with optional partial profit-taking

---

## Quick start

### 1. Clone and set up

```bash
git clone https://github.com/YOUR_USER/tradebot-main.git
cd tradebot-main
cp .env.example .env   # edit .env with your values
```

#### Linux / macOS

```bash
./create_venv.sh
source .venv/bin/activate
```

#### Windows

```bat
create_venv.bat
.venv\Scripts\activate
```

### 2. Run in demo mode (no API keys needed)

```bash
python -m tradebot.cli scan            # see scored candidates
python -m tradebot.cli trade-once      # advance one step, buy/sell
python -m tradebot.cli dashboard       # web UI at http://127.0.0.1:8008
```

### 3. Refresh external signals

```bash
python -m tradebot.cli refresh-signals    # refresh all sources at once
python -m tradebot.cli refresh-congress   # congressional PTR trades
python -m tradebot.cli refresh-sec        # SEC filings
python -m tradebot.cli refresh-earnings   # earnings calendar
python -m tradebot.cli refresh-macro      # CPI and FOMC dates
```

### 4. Export and import learning weights (brain)

Back up your bot's learned analyzer weights to a JSON file and restore them later or on another instance:

```bash
python -m tradebot.cli export-brain              # writes brain.json
python -m tradebot.cli export-brain --out my.json # custom filename
python -m tradebot.cli import-brain              # reads brain.json
python -m tradebot.cli import-brain --file my.json
```

The exported JSON looks like:

```json
{
  "exported_at": "2026-03-31T12:00:00+00:00",
  "learning": {
    "decision_support": { "wins": 5, "losses": 2, "total_return": 0.35, "weight": 1.42 },
    "momentum":         { "wins": 3, "losses": 1, "total_return": 0.20, "weight": 1.25 },
    "reversion":        { "wins": 2, "losses": 3, "total_return": -0.10, "weight": 0.85 },
    "risk":             { "wins": 4, "losses": 0, "total_return": 0.15, "weight": 1.30 }
  }
}
```

This is useful when redeploying on Railway (ephemeral filesystem) or sharing a trained brain between environments.

---

## Broker modes

| Mode | Env var | API keys needed? | Description |
|------|---------|------------------|-------------|
| Demo | `BROKER_MODE=demo` (default) | No | Deterministic market simulator |
| Paper | `BROKER_MODE=paper` | Yes | Alpaca paper trading |
| Live | `BROKER_MODE=live` | Yes | Alpaca live trading (real money) |

---

## Environment variables

All settings live in `tradebot/config.py`. Copy `.env.example` to `.env` and edit your values there. Keep `.env` out of Git.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `BROKER_MODE` | `demo` | `demo`, `paper`, or `live` |
| `ALPACA_KEY_ID` | _(empty)_ | Alpaca API key (required for paper/live) |
| `ALPACA_SECRET_KEY` | _(empty)_ | Alpaca secret key (required for paper/live) |
| `DATA_DIR` | `./data` | Directory for SQLite DB and demo state |
| `AUTO_TRADE_ENABLED` | `true` | Enable auto-trade loop on dashboard start |
| `AUTO_TRADE_INTERVAL_MINUTES` | `30` | Minutes between auto-trade cycles |
| `STARTING_CASH` | `100000` | Demo mode starting cash |
| `DEMO_SEED` | `42` | Seed for deterministic demo market |

### Risk and position sizing

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_TOTAL_CAPITAL` | `500` | Max capital deployed across all positions |
| `MAX_OPEN_POSITIONS` | `5` | Max simultaneous open positions |
| `MAX_NEW_POSITIONS_PER_RUN` | `3` | Max new buys per trade cycle |
| `RISK_PER_TRADE_PCT` | `0.04` | Fraction of capital risked per trade |
| `MAX_POSITION_PCT` | `0.25` | Max fraction of capital in one position |
| `STOP_LOSS_PCT` | `0.12` | Stop-loss distance (also accepts `STOP_LOSS`) |
| `MIN_REWARD_RISK` | `1.2` | Minimum reward-to-risk ratio |
| `USE_BROKER_PROTECTIVE_ORDERS` | `true` | Submit bracket orders to Alpaca |
| `MIN_HOLD_DAYS` | `0` | Minimum days before target exit fires |
| `MAX_HOLD_DAYS` | `0` | Time-stop in days (0 = disabled) |
| `PDT_COOLDOWN_HOURS` | `20` | Pattern day trade cooldown |
| `REBUY_COOLDOWN_HOURS` | `48` | Hours before re-buying a recently sold symbol |

### Partial profit and trailing stop

| Variable | Default | Description |
|----------|---------|-------------|
| `PARTIAL_PROFIT_ENABLED` | `true` | Sell half at the partial-profit threshold |
| `PARTIAL_PROFIT_PCT` | `15` | Percent gain to trigger partial sell |
| `PARTIAL_SELL_FRACTION` | `0.5` | Fraction of position to sell at partial profit |

### Scanning

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_STOCK_PRICE` | `10` | Max price for scan universe |
| `MIN_STOCK_PRICE` | `2` | Min price for scan universe |
| `SCAN_LIMIT` | `200` | Max symbols to scan per cycle |
| `CANDIDATE_LIMIT` | `30` | Max candidates to score |
| `MIN_DOLLAR_VOLUME` | `1000000` | Min daily dollar volume filter |
| `LOOKBACK_DAYS` | `80` | Days of price history for analyzers |
| `SCAN_UNIVERSE` | _(empty)_ | Comma-separated override list of symbols |

### Inverse ETF hedging

| Variable | Default | Description |
|----------|---------|-------------|
| `INVERSE_ETFS_ENABLED` | `true` | Enable inverse ETF hedging |
| `INVERSE_ETFS` | `SPXS,SQQQ,SDOW,SH,PSQ,DOG,SPXU,TECS` | Inverse ETF tickers |

### Congress trades

| Variable | Default | Description |
|----------|---------|-------------|
| `CONGRESS_REPORT_URLS` | _(empty)_ | Comma-separated official PTR PDF URLs |
| `CONGRESS_MAX_PRICE` | value of `MAX_STOCK_PRICE` | Max price for congress trade filter |
| `CONGRESS_TRADE_LIMIT` | `20` | Max congress trades to display |
| `CONGRESS_SIGNAL_WINDOW_DAYS` | `45` | Lookback window for congress signal |
| `CONGRESS_FRESHNESS_HOURS` | `24` | Max age before source is stale |
| `CONGRESS_MIN_RECORDS` | `1` | Min cached records to trust source |
| `CONGRESS_RETRY_MINUTES` | `15` | Backoff after failure |
| `CONGRESS_OVERRIDE_MODE` | `auto` | `auto`, `disabled`, `ignore-backoff`, or `trusted` |
| `DECISION_SUPPORT_CONGRESS_WEIGHT` | `1.0` | Weight of congress signal in scoring |

### SEC filings

| Variable | Default | Description |
|----------|---------|-------------|
| `SEC_USER_AGENT` | _(empty)_ | Contact string for SEC API (required) |
| `SEC_SIGNAL_WINDOW_DAYS` | `30` | Lookback window for SEC signal |
| `SEC_FILING_LIMIT_PER_SYMBOL` | `20` | Max filings cached per symbol |
| `SEC_FRESHNESS_HOURS` | `24` | Max age before source is stale |
| `SEC_MIN_RECORDS` | `1` | Min cached records to trust source |
| `SEC_RETRY_MINUTES` | `15` | Backoff after failure |
| `SEC_OVERRIDE_MODE` | `auto` | `auto`, `disabled`, `ignore-backoff`, or `trusted` |
| `DECISION_SUPPORT_SEC_WEIGHT` | `1.0` | Weight of SEC signal in scoring |

### Earnings

| Variable | Default | Description |
|----------|---------|-------------|
| `ALPHA_VANTAGE_API_KEY` | _(empty)_ | Alpha Vantage key for earnings calendar |
| `EARNINGS_SIGNAL_WINDOW_DAYS` | `21` | Forward window for upcoming earnings |
| `EARNINGS_FRESHNESS_HOURS` | `24` | Max age before source is stale |
| `EARNINGS_MIN_RECORDS` | `1` | Min cached records to trust source |
| `EARNINGS_RETRY_MINUTES` | `15` | Backoff after failure |
| `EARNINGS_OVERRIDE_MODE` | `auto` | `auto`, `disabled`, `ignore-backoff`, or `trusted` |
| `DECISION_SUPPORT_EARNINGS_WEIGHT` | `1.0` | Weight of earnings signal in scoring |

### Macro events

| Variable | Default | Description |
|----------|---------|-------------|
| `MACRO_SIGNAL_WINDOW_DAYS` | `7` | Forward window for CPI/FOMC events |
| `MACRO_FRESHNESS_HOURS` | `24` | Max age before source is stale |
| `MACRO_MIN_RECORDS` | `1` | Min cached records to trust source |
| `MACRO_RETRY_MINUTES` | `15` | Backoff after failure |
| `MACRO_OVERRIDE_MODE` | `auto` | `auto`, `disabled`, `ignore-backoff`, or `trusted` |
| `DECISION_SUPPORT_MACRO_WEIGHT` | `1.0` | Weight of macro signal in scoring |

### Short volume

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYGON_API_KEY` | _(empty)_ | Polygon.io key for short-volume data |
| `SHORT_VOLUME_SIGNAL_ENABLED` | `true` | Enable short-volume signal |
| `DECISION_SUPPORT_SHORT_VOLUME_WEIGHT` | `1.0` | Weight of short-volume signal |

### Dashboard

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHBOARD_HOST` | `0.0.0.0` | Dashboard bind address |
| `PORT` / `DASHBOARD_PORT` | `8008` | Dashboard port (Railway injects `PORT`) |
| `ANALYZER_MODE` | `embedded` | `embedded` or `subprocess` |

---

## Deploy to Railway

### Option A: One-click from GitHub

1. Push this repo to a GitHub repository
2. Go to [railway.com/new](https://railway.com/new) and select **Deploy from GitHub repo**
3. Pick your repo -- Railway detects `railway.json` automatically
4. Add environment variables in the Railway dashboard (see table above)
5. (Recommended) Attach a **Volume** mounted at `/data` and set `DATA_DIR=/data` so the SQLite database survives redeploys

### Option B: Railway CLI

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

Then set your env vars with `railway variables set KEY=VALUE`.

### Railway tips

- Run exactly **one instance**. SQLite plus in-process scheduling is not designed for multiple replicas.
- Mount a persistent volume at `/data` and set `DATA_DIR=/data`.
- Start with `AUTO_TRADE_ENABLED=false` and use `trade-once` manually first.
- Keep live risk tight: `MAX_TOTAL_CAPITAL=1000`, `MAX_OPEN_POSITIONS=1`, `MAX_NEW_POSITIONS_PER_RUN=1`.
- Export your brain before redeploying: `python -m tradebot.cli export-brain`, then import after.

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

---

## Architecture

```text
tradebot/
  cli.py              Command-line interface (scan, trade-once, export-brain, etc.)
  config.py            Settings dataclass, reads all env vars
  dashboard.py         FastAPI web dashboard with auto-trade scheduler
  db.py                SQLite persistence: trades, positions, learning, signals
  engine.py            Core trading engine: scan, score, buy, sell, exit logic
  models.py            Pydantic models (Candidate, Position, etc.)
  providers.py         Broker abstraction (Demo simulator, Alpaca paper/live)
  analytics.py         Embedded MCP analyzers (momentum, reversion, risk)
  universe.py          Stock universe builder and filtering
  congress.py          Congressional PTR trade parser
  sec.py               SEC EDGAR filing fetcher
  earnings.py          Alpha Vantage earnings calendar
  macro.py             CPI and FOMC calendar scraper
  polygon.py           Polygon.io short-volume data
  email_report.py      Email reporting via Resend API
  mcp_bridge.py        Bridge for subprocess MCP analyzer servers
  mcp_servers/         Standalone MCP analyzer server implementations
    momentum_server.py
    reversion_server.py
    risk_server.py
    decision_support_server.py
  templates/
    index.html         Dashboard HTML template
```

---

## Running tests

```bash
python run_tests.py
```

Convenience wrappers: `./run_tests.sh` (Linux/macOS) or `run_tests.bat` (Windows).

The repo uses `run_tests.py` instead of `pytest` directly to avoid temp-directory permission issues on Windows/OneDrive setups.

---

## Git helper

```bash
# Windows
push_git.bat "your commit message"

# Linux / macOS
./push_git.sh "your commit message"
```

Optional second arg for branch name, third for remote name.

---

## Notes

- `trade-once` advances the demo market one step, checks exits, scans, and buys fresh candidates.
- The learning loop is intentionally simple and transparent.
- This is a starter system, not financial advice, and not a promise of profits.
