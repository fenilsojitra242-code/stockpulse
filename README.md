# ⚡ StockPulse — Dynamic Stock Market Early-Warning System & ML Reversal Engine

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Scikit-Learn](https://img.shields.io/badge/scikit--learn-1.4%2B-F7931E.svg?logo=scikit-learn&logoColor=white)](https://scikit-learn.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests: 164 Passed](https://img.shields.io/badge/Tests-164%20Passed-brightgreen.svg)]()

> **StockPulse** is a production-grade financial web application for real-time stock market monitoring, technical indicator analysis, and probabilistic early-warning detection. It features a dual-layer Supertrend analysis engine, calibrated machine learning reversal classification, multi-channel alerting, and an interactive single-page dashboard.

---

## 📌 Architectural Overview

StockPulse is architected around an asynchronous, non-blocking pipeline separating real-time market data ingestion, technical feature computation, machine learning inference, and stateful notification delivery:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                             CLIENT LAYER (BROWSER)                          │
│   • Single-Page Dashboard (Live Market, Interactive Chart.js Visualization) │
│   • Background Web Worker Poller (Immune to Tab Throttling)                 │
│   • Desktop Web Notifications API & Real-Time In-App Alert Banners          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ HTTP / REST / WebSockets
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                           FASTAPI APPLICATION CORE                          │
│   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────────┐  │
│   │ Authentication     │ │ Market Data Router │ │ Alert & History Router │  │
│   │ (JWT / Bcrypt)     │ │ (/api/stocks)      │ │ (/api/alerts)          │  │
│   └────────────────────┘ └────────────────────┘ └────────────────────────┘  │
└──────────┬───────────────────────────┬───────────────────────────┬──────────┘
           │                           │                           │
┌──────────▼──────────┐     ┌──────────▼──────────┐     ┌──────────▼──────────┐
│ TECHNICAL INDICATOR │     │ ML INFERENCE ENGINE │     │ STATEFUL SCHEDULER  │
│   FEATURE ENGINE    │     │  (SCIKIT-LEARN)     │     │    (APSCHEDULER)    │
│ • Supertrend (ATR)  │     │ • Random Forest     │     │ • 10s Continuous    │
│ • RSI, MACD, EMAs   │     │   Classifier        │     │   Monitoring Loop   │
│ • Dynamic Proximity │     │ • Dynamic Multi-    │     │ • Duplicate Event   │
│ • Market Regimes    │     │   Horizon Reversals │     │   Suppression       │
└──────────┬──────────┘     └──────────┬──────────┘     └──────────┬──────────┘
           │                           │                           │
┌──────────▼───────────────────────────▼───────────────────────────▼──────────┐
│                             STORAGE & FEEDS                                 │
│   • Yahoo Finance Data Feed (In-Flight Deduplication & Multi-Tier Caching)  │
│   • Persistent SQLite Database (data/stockpulse.db)                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Key System Features

### 1. Dual-Layer Supertrend & Reversal Engine
- **Confirmed Trend Detection**: Derived strictly from completed candles using Wilder's Average True Range (ATR) and adaptive multiplier bands.
- **Early-Warning Proximity Zone**: Triggers an alert when developing market prices enter a configurable percentage proximity window (e.g., within 1.5% of the band) *before* the candle closes.
- **Confirmed Reversals**: Raised only when completed candle closes cross the opposite Supertrend boundary.
- **Deterministic Separation**: Early warnings are never conflated with confirmed reversals; signals carry signed percentage distances and directional momentum indicators.

### 2. Machine Learning Reversal & Probability Scoring
- **Dynamic Multi-Horizon Formulation**:
  $$\text{Features (OHLC, ATR, RSI, Volatility, Regime)} + \text{Target \%} + \text{Warning Horizon} \longrightarrow P(\text{Reversal})$$
- Evaluates market conditions using a trained Random Forest model (`models/supertrend_reversal_model.pkl`) to assign probabilistic confidence scores to emerging trend shifts.
- Distinct fallback mechanisms for off-market or insufficient candle sequences, preserving data integrity without misrepresenting fallback states as live model outputs.

### 3. Market-Data Reliability & Provider Safeguards
- **Zero Synthetic Data Policy**: StockPulse strictly ingests genuine provider data. No synthetic prices, interpolated candles, or fabricated timestamps are ever generated.
- **Explicit Market States**:
  - `LIVE`: During open exchange sessions with timely updates.
  - `DELAYED`: Older feed updates beyond the fresh threshold.
  - `STALE`: Outdated data outside acceptable intraday tolerance.
  - `CLOSED`: Outside regular market hours, weekends, and holidays.
  - `DATA_UNAVAILABLE`: Network failure or empty provider responses.
- **Multi-Year Algorithmic Calendar**:
  - Computes US holidays (NYSE/NASDAQ) and Indian national holidays (NSE/BSE) algorithmically using Gregorian Computus (Good Friday) and Nth weekday rules without external dependencies.
- **In-Flight Request Deduplication**: Prevents duplicate concurrent calls to Yahoo Finance when multiple background jobs or clients request identical tickers.

### 4. Background Monitoring & Alert Dispatcher
- **Continuous Scheduler**: Powered by APScheduler running every 10 seconds across active watchlists.
- **Duplicate Event Suppression**: Notifications are keyed by `(user_id, symbol, interval, timestamp, direction_flip)`. Once triggered for a specific candle cycle, duplicate alerts are automatically suppressed.
- **Multi-Channel Delivery**: In-app popup notifications, audio chimes, browser desktop notifications, and historical audit logging.

---

## 🛠️ Technology Stack

| Layer | Component | Details |
|---|---|---|
| **Backend Framework** | [FastAPI](https://fastapi.tiangolo.com) | Python 3.10+, asynchronous route handlers, Pydantic v2 schemas |
| **ASGI Server** | [Uvicorn](https://www.uvicorn.org) | Production-ready high-concurrency server |
| **Database & ORM** | [SQLAlchemy](https://www.sqlalchemy.org) / SQLite | Relational schema with WAL mode, threading guards, and migrations |
| **Task Scheduling** | [APScheduler](https://apscheduler.readthedocs.io) | Multi-stock recurring intraday monitoring loops |
| **Machine Learning** | [Scikit-Learn](https://scikit-learn.org) | Random Forest Classifiers, feature scaling, model persistence |
| **Data Processing** | [Pandas](https://pandas.pydata.org) & [NumPy](https://numpy.org) | Vectorized technical indicators (ATR, Supertrend, RSI, MACD) |
| **Market Data Feed** | [yfinance](https://github.com/ranaroussi/yfinance) | Genuine quote & candlestick ingestion with multi-tier caching |
| **Authentication** | [PyJWT](https://pyjwt.readthedocs.io) & [Passlib (Bcrypt)](https://passlib.readthedocs.io) | Salted bcrypt password hashing (`$2b$12$...`) & JWT sessions |
| **Frontend** | Vanilla JS (ES6+) & Modern CSS3 | Single-page architecture, Chart.js, Plus Jakarta Sans typography |

---

## 📋 Exchange Sessions & Market Limitations

StockPulse supports both Indian (NSE/BSE) and US (NYSE/NASDAQ) equities.

| Exchange | Timezone | Regular Trading Hours | Status Behavior |
|---|---|---|---|
| **NSE / BSE** | `Asia/Kolkata` (IST, UTC+5:30) | 09:15 – 15:30 IST | Reports `CLOSED` outside this window and on holidays |
| **NYSE / NASDAQ** | `America/New_York` (ET, UTC-4/5) | 09:30 – 16:00 ET | Handles Daylight Saving Time (EDT/EST) automatically |

### Provider Limitations:
- **Free-Tier Ingestion**: Real-time intraday data relies on Yahoo Finance public endpoints. Providers may experience intermittent latency or rate-limiting under high concurrency.
- **Intraday Caching**: To protect against rate-limiting, responses are cached with a 5-second TTL during live sessions and 30-minute TTL when the market is closed.

---

## 🚦 Supertrend Early Warnings vs. Confirmed Reversals

```text
                  BUY TREND ACTIVE (Price above Supertrend Line)
                  ══════════════════════════════════════════════
    High: ₹2,520
               • (Live Price: ₹2,506)
               │
               ▼  Distance: +0.25% (Inside 1.5% Proximity Zone)
               ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈  [Early Warning Triggered]
               ▲  Supertrend Band: ₹2,500
               │
    ───────────┴────────────────────────────────────────────────
               If completed candle close breaches below ₹2,500:
               ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈  [Confirmed Reversal to SELL]
```

1. **Early Warning**: Price enters the user-defined percentage proximity zone, alerting the trader to an impending test of the support/resistance line.
2. **Confirmed Reversal**: Only occurs when the closing price of a completed candle crosses the band, flipping the trend direction.

---

## 💻 Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/fenilsojitra242-code/stockpulse.git
cd stockpulse
```

### 2. Create and Activate Virtual Environment
```bash
# Windows
python -m venv .venv
.\.venv\Scripts\activate

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the template configuration file:
```bash
cp .env.example .env
```
Key configuration settings in `.env`:
```env
HOST=127.0.0.1
PORT=8000
ENVIRONMENT=production
# Optional SMTP configuration for email alerts:
# BREVO_API_KEY=...
# BREVO_SENDER_EMAIL=...
```

### 5. Launch the Application
```bash
# Using uvicorn directly
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Or using the Windows launcher script
run.bat
```

Navigate to:
- **Web Dashboard**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Interactive OpenAPI Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Health Check Endpoint**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## 🌐 Production Deployment on Render

Render Web Services run in ephemeral containers that restart on idle periods or redeployments. To ensure accounts, passwords, alerts, and settings persist permanently across all restarts, StockPulse supports two production persistence architectures:

### Option A: Render Persistent Disk (SQLite — Recommended for Single-Instance)

> **Important**: Render Persistent Disks require a Render **Starter** plan ($7/mo + $0.25/GB disk) because Render Free Web Services do not support attaching persistent disks.

#### 1. Configure Persistent Disk in Render Dashboard
1. Go to your Web Service in [dashboard.render.com](https://dashboard.render.com).
2. Upgrade service plan to **Starter** ($7/mo).
3. Navigate to **Disks** tab → Click **Add Disk**.
4. Configure disk settings:
   - **Name**: `stockpulse-data`
   - **Mount Path**: `/var/data`
   - **Size**: `1 GB` (or more as needed)
5. Save changes.

#### 2. Set Environment Variables
In the **Environment** tab, add:
- `DATABASE_PATH` = `/var/data/stockpulse.db`
- `PERSISTENT_DATA_DIR` = `/var/data`
- `ENVIRONMENT` = `production`

*(Alternatively, connect your Git repository and deploy using the included `render.yaml` Blueprint which configures the disk and environment variables automatically).*

#### 3. Migrate Existing Users & Data to Persistent Storage
If you have an existing local or pre-existing SQLite database (`data/stockpulse.db`):
```bash
# Migrate to persistent disk with WAL flush, integrity checks, and backup:
python app/database/migrate_to_persistent_disk.py --source data/stockpulse.db --target /var/data/stockpulse.db
```

---

### Option B: Managed PostgreSQL (Render Postgres or Neon Free)

#### 1. Create a PostgreSQL Database
1. In [dashboard.render.com](https://dashboard.render.com), click **New +** → **PostgreSQL** (or create a free Neon database at [neon.tech](https://neon.tech)).
2. Name it `stockpulse-db` and provision it.
3. Copy the **Internal Database URL** (for Render) or connection string.

#### 2. Migrate Existing SQLite Data to PostgreSQL
```bash
python app/database/migrate_to_postgres.py --target "<DATABASE_URL>"
```

#### 3. Configure Render Web Service Environment
- `DATABASE_URL` = `<DATABASE_URL>`
- `ENVIRONMENT` = `production`

---

## 🧪 Running the Test Suite

StockPulse includes a comprehensive regression and validation suite covering unit, integration, stress, and algorithmic checks:

```bash
# Run the complete test suite
python -m unittest discover -s tests -p "test_*.py"

# Run the benchmark profiler (isolated database)
python tests/benchmark_suite.py
```

### Test Coverage Highlights (162 Tests, 0 Failures):
- `tests/test_supertrend_reversal_predictor.py` — Multi-horizon reversal inference.
- `tests/test_phase4_production_validation.py` — Multi-year holidays, bcrypt hashing, deduplication.
- `tests/test_phase3_reliability_stress.py` — Timeout resilience, closed market states.
- `tests/test_supertrend_early_warning.py` — Signed distance formulas and proximity zones.

---

## 🔒 Security & Privacy

- **Password Storage**: Passwords are never stored in plaintext; all credentials are encrypted using industry-standard bcrypt hashes with salt rounds $\ge 12$.
- **Database Isolation**: The test and benchmark suites use dedicated, ephemeral SQLite databases created in temporary directories to prevent data corruption or leaking private records.
- **Credential Hygiene**: The active `.env` file, database files (`*.db`), virtual environments, and temporary logs are excluded from distribution packages (`package_client.ps1`).

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
