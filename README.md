# ⚡ StockPulse — Live Stock Monitoring & Dynamic Alert Dashboard

> **A professional single-page real-time stock market monitoring and probabilistic early-warning web application. Users can select any stock, view complete live intraday information, configure dynamic movement conditions, track predictions in a dedicated side monitoring panel, and start or stop alerts seamlessly.**

---

## 📌 Single-Page Dashboard Architecture

StockPulse is designed as a **unified, high-performance single-page stock dashboard** adhering to the following structure:

```text
┌───────────────────────────────────────────────────────────────┐
│                     STOCKPULSE                                │
│ Market: OPEN / CLOSED                 Live System Active ●    │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  STOCK SEARCH                                                 │
│  [ Search Stock Symbol or Company Name ]                      │
│  Popular: [ TCS ] [ INFY ] [ RELIANCE ] [ HDFCBANK ] ...      │
│                                                               │
├───────────────────────────────────────┬───────────────────────┤
│                                       │                       │
│       LIVE STOCK INFORMATION          │   ACTIVE MONITORING   │
│                                       │   (Dedicated Sidebar) │
│  TCS                                  │   ┌─────────────────┐ │
│  ₹3,421.50                            │   │ TCS             │ │
│  +₹48.20 (+1.43%)                     │   │ Price: ₹3,421.50│ │
│                                       │   │ Move:  +3.42%   │ │
│  ┌───────────────────────────────┐    │   │ Target:+5.00%   │ │
│  │                               │    │   │ Remain: 1.58%   │ │
│  │    LIVE 1M PRICE CHART        │    │   │ Horizon: 10 min │ │
│  │                               │    │   │ ML Prob: 78.4%  │ │
│  └───────────────────────────────┘    │   │ ● MONITORING    │ │
│                                       │   │ [STOP MONITOR]  │ │
│  Open   Day High   Day Low   Volume   │   └─────────────────┘ │
│                                       │                       │
├───────────────────────────────────────┴───────────────────────┤
│                                                               │
│              ALERT CONFIGURATION                              │
│                                                               │
│ Stock       [ TCS ]                                           │
│ Direction   [ UP ] [ DOWN ] [ BOTH ]                          │
│ Target %    [ 5.0 % ]   (+1%, +2.5%, +5%, +7.5%, +10%)        │
│ Warning     [ 10 min ]  (2m, 5m, 10m, 15m, 30m)               │
│ Probability [ 75% ]                                           │
│ Interval    [ 1 min ] [ 2 min ] [ 5 min ]                     │
│ Notification[ In-App Popup ] [ Browser Feed ] [ Email ]       │
│ Target Price[ ₹3,592.58 (Auto-calculated) ]                   │
│                                                               │
│                  [ START MONITORING ]                         │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│              TECHNICAL ANALYSIS                               │
│                                                               │
│ RSI | MACD | SMA (5/15/30) | EMA (9/21) | ATR | Volatility     │
│ Momentum | Rate of Change | 1m/3m/5m/10m Returns             │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│              COMPLETE STOCK DETAILS                           │
│                                                               │
│ 52W High | 52W Low | Market Cap | Today's Volume              │
│ 20D Avg Vol | Prev Close | Day's Range | Trading Status       │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│              ALERT HISTORY & OUTCOME VERIFICATION             │
│                                                               │
│ Stock | Direction | Target % | Target Price | Window | Status │
│ Probability | Result | Duration | Started At | Notes          │
└───────────────────────────────────────────────────────────────┘
```

---

## 🚀 Key Innovations & Features

### 1. Dedicated Side Monitoring Panel
- When **START MONITORING** is clicked, a dedicated card appears immediately in the side panel.
- Displays:
  - Stock Symbol
  - Live Current Price
  - Current Movement %
  - Target Movement %
  - Remaining % needed to hit target
  - Warning Window (minutes)
  - Target Price (₹)
  - Real-Time ML Probability meter
  - Status indicator: `● MONITORING`, `🚨 EARLY WARNING`, `⚡ TRIGGERED`, `⏱️ COOLDOWN`
- Prominent **[STOP MONITORING]** button for each active stock card:
  1. Immediately stops background monitoring for that alert.
  2. Halts notification dispatches.
  3. Transitions status to `STOPPED`.
  4. Records the exact session in history with calculated duration (`XXm YYs`).
  5. Preserves all configuration and outcome metrics.

### 2. Live Stock Visualization & Charting
- 1-minute intraday candlestick/line chart with 15-period Simple Moving Average overlay.
- High/Low/Open/Volume quick metrics bar.
- Automatic live updates synchronized with the configured interval (1m, 2m, 5m).

### 3. Comprehensive Technical Analysis & Stock Details
- **Technical Indicators**:
  - RSI (14-period Relative Strength Index)
  - MACD Line, Signal Line, and MACD Histogram
  - Moving Averages: SMA (5, 15, 30) & EMA (9, 21)
  - ATR (Average True Range) & Normalized ATR %
  - Volatility (10m and 30m rolling standard deviation)
  - Momentum & 5m Rate of Change (ROC)
  - 1m, 3m, 5m, 10m, 15m Returns
- **Complete Stock Details**:
  - 52-Week High & Low
  - Market Capitalization
  - Today's Trading Volume & 20-Day Average Volume
  - Previous Close & Day's Range (High - Low)

### 4. Dynamic Multi-Horizon ML Formulation
- Problem formulation:
  $$\text{Market Features} + \text{Target \%} + \text{Warning Minutes} + \text{Direction} \longrightarrow \text{Calibrated Probability}$$
- Fully dynamic percentage targets (e.g. 1%, 2.5%, 5%, 7.5%, 10%) and time windows (e.g. 2m, 5m, 10m, 15m, 30m) — **never hardcoded**.
- Calibrated Random Forest model achieving **0.9419 ROC-AUC** on time-based validation data.

### 5. Multi-Channel Notifications & Simulation Lab
- **Authenticated Email Alert System**: Full SMTP authentication (Gmail, Outlook/365, Yahoo, Custom SMTP) with STARTTLS/SSL, instant live verification, and delivery of HTML early-warning notices to inboxes.
- **Customized In-App Popups & Audio Chimes**: Instant visual popup notices and audio alerts on the dashboard.
- **Browser/Dashboard Feed**: In-app live stream of all early-warning trigger events.
- **Simulation Lab**: Allows users to inject simulated price spikes (+3.8% surge or -3.5% drop) to test the early-warning alert engine 24/7, even during off-market hours.

---

## 📡 REST API Endpoints

### Email Authentication & System Settings
- `GET /api/system/email/config` — Get current SMTP authentication status & masked credentials.
- `POST /api/system/email/config` — Save SMTP host, port, username, password, recipient.
- `POST /api/system/email/test` — Test authentication & dispatch verification email.
- `DELETE /api/system/email/config` — Reset / clear saved email credentials.

### Stocks
- `GET /api/stocks` — List all registered stocks with live prices & change %.
- `GET /api/stocks/{symbol}` — Complete stock details, indicators, and recent 1m candles.
- `GET /api/stocks/{symbol}/live` — Lightweight fast live quote with market status.
- `POST /api/stocks` — Add a custom stock ticker.

### Alerts
- `POST /api/alerts` — Create and start an alert monitoring session.
- `GET /api/alerts` — List all alerts.
- `GET /api/alerts/active` — Active monitoring sessions for the dedicated sidebar (with live price, movement, remaining %, and probability).
- `GET /api/alerts/{id}` — Single alert details.
- `POST /api/alerts/{id}/start` — Resume / start monitoring.
- `POST /api/alerts/{id}/stop` — Stop monitoring, calculate duration, and record to history.
- `DELETE /api/alerts/{id}` — Delete an alert.
- `POST /api/alerts/predict` — On-demand dynamic prediction preview.

### History & Evaluation
- `GET /api/history` — Historical monitoring sessions with ground-truth resolution (`Target reached`, `Stopped`, `Not reached`).
- `GET /api/history/stats` — Empirical accuracy KPIs (hit rate %, counts, duration).

---

## 🚀 Running the Application

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Start the Server
```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 3. Open the Dashboard
Navigate to:
- **Web Dashboard**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **API Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)


## Monitoring State Machine

Each alert is an independent monitoring task:

`MONITORING -> EARLY_WARNING -> SUCCESS (task complete)`

or

`MONITORING -> EARLY_WARNING -> FAILED -> MONITORING`

A target reached before an early-warning cycle is **not** treated as prediction success. Once an alert reaches `SUCCESS`, only that alert stops; the background scheduler continues evaluating all other active alerts. After `FAILED`, the alert starts a fresh prediction cycle immediately, with its previous cooldown cleared.
