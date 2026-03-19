# Equities Trading Bot

An automated algorithmic trading system built in Python, designed for intraday trading on gold (and other equities) via the IG broker platform. The bot runs continuously, scanning markets, generating trade signals from price-action patterns, and executing trades — all without manual intervention.

---

## Features

- **Automated trade execution** via the IG REST API (market orders, stop loss, take profit)
- **Multiple strategies** selectable via config — including intraday gold, simplicity (first candle rule), MACD, Bollinger Bands, advanced momentum, and smart sentiment
- **Signal confidence scoring** using MACD, RSI, EMA alignment, ADX, ATR, and volume
- **Telegram alerts** — sends formatted trade signal notifications with confidence scores
- **Platform signal distribution** — broadcasts signals to an external social trading REST API
- **Backtesting** — replay any strategy against historical data with a date range
- **Paper trading mode** — test live without real orders
- **Market data from multiple sources** — IG, yfinance, or Alpha Vantage, switchable via config

---

## Strategies

| Strategy | Description |
|---|---|
| `intraday_gold` | Price-action patterns (engulfing, pin bar, breakout/retest, false breakout) at pivot and swing S/R levels, confirmed by EMA 50/200, ATR stops |
| `simplicity` | First candle rule — uses the 14:30–15:00 UK candle high/low to trade breakouts, confirmed by Fair Value Gap |
| `simple_macd` | Classic MACD crossover |
| `simple_boll_bands` | Bollinger Bands mean-reversion |
| `weighted_avg_peak` | Weighted average peak detection |
| `advanced_momentum` | MACD + RSI + ADX + ATR trailing stops, squeeze detection |
| `smart_sentiment` | Sentiment-driven signals |
| `strategy_manager` | Switches between strategies at configured times (e.g. intraday_gold in the morning, simplicity at NY open) |

---

## Project Structure

```
equitiestradingbot/
├── components/
│   ├── broker/             # IG, yfinance, Alpha Vantage interfaces + broker abstraction
│   ├── platform/           # REST API signal sender + signal frequency manager
│   ├── telegram/           # Telegram bot + signal rate limiter
│   ├── configuration.py    # TOML config loader
│   ├── market_provider.py  # Market source (list, API, watchlist)
│   ├── backtester.py       # Backtesting engine
│   └── time_provider.py    # Market hours + wait logic
├── strategies/
│   ├── intraday_gold_strategy.py
│   ├── simplicity.py
│   ├── simple_macd.py
│   ├── signal_confidence.py  # Multi-indicator confidence scorer
│   └── ...
├── interfaces/             # Market, Position, MarketHistory data models
└── tradingbot.py           # Main bot loop
config/
├── live_trading_bot.toml   # Main config (gitignored)
test/                       # Unit tests (pytest)
```

---

## Tech Stack

| Area | Tools |
|---|---|
| Language | Python 3.13 |
| Market data | IG REST API, yfinance, Alpha Vantage |
| Trade execution | IG REST API |
| Notifications | Telegram Bot API |
| Data processing | pandas, numpy, scipy |
| Config | TOML |
| Testing | pytest, requests-mock |
| Packaging | Poetry |

---

## Setup

### 1. Install dependencies

```bash
poetry install
```

Or with pip:

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy the example config and fill in your credentials:

```bash
cp config/live_trading_bot.example.toml config/live_trading_bot.toml
```

You will need:
- **IG account** — API key, username, password, account ID
- **Telegram bot** — bot token and chat ID (optional, for alerts)
- **Platform API** — base URL and API key (optional, for signal distribution)

### 3. Run

**Full bot (continuous loop):**
```bash
python -m equitiestradingbot --config config/live_trading_bot.toml
```

**Single pass (one iteration, good for testing):**
```bash
python -m equitiestradingbot --config config/live_trading_bot.toml --single-pass
```

**Close all open positions:**
```bash
python -m equitiestradingbot --config config/live_trading_bot.toml --close-positions
```

**Backtest a market:**
```bash
python -m equitiestradingbot --config config/live_trading_bot.toml --backtest MARKET_ID --start 2024-01-01 --end 2024-12-31
```

---

## Running Tests

```bash
pytest test/
```

---

## Configuration Reference

Key settings in `live_trading_bot.toml`:

| Setting | Description |
|---|---|
| `max_account_usable` | Max % of account to use (e.g. `50`) |
| `paper_trading` | `true` to run without real orders |
| `spin_interval` | Seconds between each market scan loop |
| `strategies.active` | Which strategy to run |
| `stocks_interface.active` | Data source: `ig_interface`, `yfinance`, or `alpha_vantage` |
| `stocks_interface.ig_interface.use_demo_account` | `true` for IG demo account |

---

## Disclaimer

This software is for educational purposes. Algorithmic trading carries significant financial risk. Past performance does not guarantee future results. Use at your own risk.
