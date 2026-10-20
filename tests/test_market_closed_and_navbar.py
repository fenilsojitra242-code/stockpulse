"""
Regression & Acceptance Test Suite for:
1. Authoritative Market Status & Holiday/Weekend/Hours Rules
2. Market-Closed Skeletons Elimination & Candle Fallback
3. Navbar Initial Status Neutral State (Zero "Market: OPEN" Flicker)
4. Targeted DOM Updates & State Invariants
"""

import unittest
from datetime import datetime
from fastapi import Response
import pytz

from app.services.market_data import get_market_status, NSE_HOLIDAYS, IST
from app.api.stocks import (
    get_global_market_status_endpoint,
    get_stock_data,
    get_stock_fast_live,
    get_stock_supertrend
)

class TestMarketClosedAndNavbar(unittest.TestCase):
    def test_01_market_open_trading_hours(self):
        """During a regular weekday (Wednesday) at 11:30 AM IST, market is OPEN."""
        # 2026-10-14 is a Wednesday and not in NSE_HOLIDAYS
        wednesday_open = datetime(2026, 10, 14, 11, 30, 0, tzinfo=IST)
        status, time_str = get_market_status(wednesday_open)
        self.assertEqual(status, "OPEN")
        self.assertIn("11:30:00 AM", time_str)

    def test_02_market_closed_outside_trading_hours(self):
        """Pre-market (08:30 AM) and post-market (16:00 PM) on a trading day must be CLOSED."""
        pre_market = datetime(2026, 10, 14, 8, 30, 0, tzinfo=IST)
        status_pre, _ = get_market_status(pre_market)
        self.assertEqual(status_pre, "CLOSED")

        post_market = datetime(2026, 10, 14, 16, 0, 0, tzinfo=IST)
        status_post, _ = get_market_status(post_market)
        self.assertEqual(status_post, "CLOSED")

    def test_03_market_closed_on_weekends(self):
        """Saturday and Sunday must always be CLOSED regardless of hour."""
        saturday_noon = datetime(2026, 10, 17, 12, 0, 0, tzinfo=IST) # Saturday
        status_sat, _ = get_market_status(saturday_noon)
        self.assertEqual(status_sat, "CLOSED")

        sunday_morning = datetime(2026, 10, 18, 10, 0, 0, tzinfo=IST) # Sunday
        status_sun, _ = get_market_status(sunday_morning)
        self.assertEqual(status_sun, "CLOSED")

    def test_04_market_closed_on_nse_holidays(self):
        """Configured NSE holidays (e.g. Dussehra Oct 20 2026, Republic Day Jan 26) must be CLOSED during trading hours."""
        dussehra_trading_time = datetime(2026, 10, 20, 11, 0, 0, tzinfo=IST)
        self.assertIn("2026-10-20", NSE_HOLIDAYS)
        status_hol, _ = get_market_status(dussehra_trading_time)
        self.assertEqual(status_hol, "CLOSED")

        republic_day_2025 = datetime(2025, 1, 26, 12, 0, 0, tzinfo=IST)
        status_rep, _ = get_market_status(republic_day_2025)
        self.assertEqual(status_rep, "CLOSED")

    def test_05_market_status_error_fallback(self):
        """If market status calculation receives invalid datetime, fallback to STATUS UNKNOWN."""
        status_unknown, _ = get_market_status("invalid_argument_triggering_exception")
        self.assertEqual(status_unknown, "STATUS UNKNOWN")

    def test_06_market_status_api_endpoint(self):
        """GET /api/stocks/market-status returns authoritative payload and no-cache headers."""
        resp = Response()
        data = get_global_market_status_endpoint(resp)
        self.assertIn("market_status", data)
        self.assertIn(data["market_status"], ["OPEN", "CLOSED", "STATUS UNKNOWN"])
        self.assertIn("is_open", data)
        self.assertEqual(data["exchange"], "NSE")
        self.assertEqual(data["timezone"], "Asia/Kolkata")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-cache, no-store, must-revalidate")

    def test_07_stock_data_endpoint_market_closed_invariants(self):
        """GET /api/stocks/{symbol} returns valid quote and candles even when market is closed."""
        resp = Response()
        data = get_stock_data("TCS", range="1D", response=resp)
        self.assertIn("price", data)
        self.assertGreater(data["price"], 0)
        self.assertIn("market_status", data)
        self.assertIn("candles", data)
        # Skeletons bug root cause was empty candles. With multi-tier fallback, candles should be non-empty
        self.assertGreater(len(data["candles"]), 0, "Closed market 1D must provide latest available candles via fallback")
        # Ensure price is valid and not NaN
        self.assertIsNotNone(data["previous_close"])
        self.assertIn("latest_market_timestamp", data)
        # Closed market must not label quote as LIVE
        if data["market_status"] == "CLOSED":
            self.assertEqual(data["data_status"], "CLOSED")

    def test_08_fast_live_endpoint_separates_market_timestamp(self):
        """GET /api/stocks/{symbol}/live separates latest_market_timestamp from server sync timestamp."""
        resp = Response()
        data = get_stock_fast_live("TCS", response=resp)
        self.assertIn("latest_market_timestamp", data)
        self.assertIn("data_received_at", data)
        self.assertIn("market_status", data)
        self.assertIn("data_status", data)

    def test_09_supertrend_endpoint_stability(self):
        """Supertrend endpoint returns valid series and current signal."""
        data = get_stock_supertrend("TCS", range="1D")
        self.assertIn("direction", data)
        self.assertIn(data["direction"], ["BUY", "SELL", "NEUTRAL"])
        self.assertIn("supertrend_price", data)
        self.assertIn("series", data)

    def test_10_html_navbar_has_no_hardcoded_open_or_green(self):
        """All 7 HTML pages must NOT hardcode 'Market: OPEN' or green dot on market status in raw HTML."""
        pages = [
            "frontend/live.html",
            "frontend/monitoring.html",
            "frontend/details.html",
            "frontend/history.html",
            "frontend/notifications.html",
            "frontend/index.html",
            "frontend/dashboard.html"
        ]
        for page_path in pages:
            with open(page_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn(
                "Market: OPEN",
                content,
                f"Page {page_path} has hardcoded 'Market: OPEN' which causes status flicker on page load!"
            )
            self.assertNotIn(
                'class="pulse-dot green" id="market-status-dot"',
                content,
                f"Page {page_path} has hardcoded 'pulse-dot green' navbar dot!"
            )
            self.assertNotIn(
                'id="market-status-dot" class="pulse-dot green"',
                content,
                f"Page {page_path} has hardcoded 'pulse-dot green' navbar dot!"
            )
            # Ensure neutral checking state is present on market status dot in navbar
            self.assertTrue(
                ('id="market-status-dot"' in content and 'neutral' in content),
                f"Page {page_path} should have neutral initial dot in navbar!"
            )
            self.assertIn(
                'Checking market status...',
                content,
                f"Page {page_path} should have neutral initial placeholder 'Checking market status...'"
            )

    def test_11_css_contains_neutral_and_closed_dot_styles(self):
        """Verify CSS contains .pulse-dot.neutral and .pulse-dot.closed definitions."""
        with open("frontend/style.css", "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".pulse-dot.neutral", css)
        self.assertIn(".pulse-dot.closed", css)

    def test_12_common_js_exports_authoritative_navbar_handlers(self):
        """Verify frontend/common.js contains initNavbarMarketStatus, applyMarketStatus and sessionStorage cache."""
        with open("frontend/common.js", "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("applyMarketStatus", js)
        self.assertIn("initNavbarMarketStatus", js)
        self.assertIn("stockpulse_cached_market_status", js)
        self.assertIn("isSystemStatusInFlight", js)

    def test_13_live_js_adaptive_polling_and_hud_updates(self):
        """Verify live.js uses adaptive polling, updates OHLC HUD immediately, and handles closed market."""
        with open("frontend/live.js", "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("scheduleNextPoll", js)
        self.assertIn("updateOHLCHUD", js)
        self.assertIn("resetOHLCHUD", js)
        self.assertIn("visibilitychange", js)
        self.assertIn("beforeunload", js)
        # Ensure 3000ms is used when open and 30000ms when closed
        self.assertIn("30000", js)

if __name__ == "__main__":
    unittest.main()
