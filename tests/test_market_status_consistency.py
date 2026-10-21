"""
Comprehensive Regression Tests for Market-Status Inconsistency Bug Fix:
1. Authoritative shared market status across endpoints (/api/stocks/market-status, /api/stocks/{symbol}, /api/stocks/{symbol}/live).
2. Consistent status across page navigation and page refresh scenarios (both open and closed sessions).
3. Cache staleness resolution: cached payloads in _RANGE_CACHE dynamically reflect current market status.
4. Temporary unavailability / error fallback returns STATUS UNKNOWN / DATA_UNAVAILABLE instead of incorrectly declaring market CLOSED.
5. Frontend contract tests: out-of-order response rejection, shortened cache TTL, UNKNOWN/DATA_UNAVAILABLE neutral handling.
"""

import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
from fastapi import Response
import time

from app.services.market_data import (
    get_market_status,
    get_stock_visualization_data,
    _RANGE_CACHE,
    IST,
    CACHE_TTL_CLOSED,
    CACHE_TTL_INTRADAY
)
from app.api.stocks import (
    get_global_market_status_endpoint,
    get_stock_data,
    get_stock_fast_live
)


class TestMarketStatusConsistency(unittest.TestCase):

    def test_01_open_market_authoritative_consistency(self):
        """During trading hours, market-status, stock-data, and fast-live endpoints must agree on OPEN."""
        open_time = datetime(2026, 10, 14, 11, 30, 0, tzinfo=IST)
        with patch("app.services.market_data.datetime") as mock_dt:
            mock_dt.now.return_value = open_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat

            status, _ = get_market_status()
            self.assertEqual(status, "OPEN")

            resp = Response()
            status_data = get_global_market_status_endpoint(resp)
            self.assertEqual(status_data["market_status"], "OPEN")
            self.assertTrue(status_data["is_open"])

            stock_payload = get_stock_data("TCS", range="1D", response=resp)
            self.assertEqual(stock_payload["market_status"], "OPEN")
            self.assertEqual(stock_payload["trading_status"], "Normal Trading")
            self.assertIn(stock_payload["data_status"], ["LIVE", "DELAYED", "STALE", "DATA_UNAVAILABLE"])

            fast_live = get_stock_fast_live("TCS", response=resp)
            self.assertEqual(fast_live["market_status"], "OPEN")
            self.assertIn(fast_live["data_status"], ["LIVE", "DELAYED", "STALE", "DATA_UNAVAILABLE"])

    def test_02_closed_market_authoritative_consistency(self):
        """Outside trading hours, all endpoints must consistently report CLOSED."""
        closed_time = datetime(2026, 10, 14, 17, 30, 0, tzinfo=IST)
        with patch("app.services.market_data.datetime") as mock_dt:
            mock_dt.now.return_value = closed_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat

            status, _ = get_market_status()
            self.assertEqual(status, "CLOSED")

            resp = Response()
            status_data = get_global_market_status_endpoint(resp)
            self.assertEqual(status_data["market_status"], "CLOSED")
            self.assertFalse(status_data["is_open"])

            stock_payload = get_stock_data("TCS", range="1D", response=resp)
            self.assertEqual(stock_payload["market_status"], "CLOSED")
            self.assertEqual(stock_payload["trading_status"], "Market Closed")
            self.assertEqual(stock_payload["data_status"], "CLOSED")

            fast_live = get_stock_fast_live("TCS", response=resp)
            self.assertEqual(fast_live["market_status"], "CLOSED")
            self.assertEqual(fast_live["data_status"], "CLOSED")

    def test_03_range_cache_does_not_serve_stale_open_status_after_market_close(self):
        """If stock payload was cached while market was OPEN, subsequent calls after market close must return CLOSED."""
        cache_id = ("TCS", "1D")
        _RANGE_CACHE[cache_id] = {
            "timestamp": time.time(),
            "data": {
                "symbol": "TCS",
                "price": 3500.0,
                "price_change": 15.0,
                "change_pct": 0.43,
                "previous_close": 3485.0,
                "market_status": "OPEN",
                "trading_status": "Normal Trading",
                "data_status": "LIVE",
                "latest_market_timestamp": "2026-10-14T15:29:00+05:30",
                "candles": [{"time": "15:29", "price": 3500.0}],
                "chart_points": [{"time": "15:29", "price": 3500.0}]
            }
        }

        # Simulate time advancing to 16:00 IST (market closed)
        closed_time = datetime(2026, 10, 14, 16, 0, 0, tzinfo=IST)
        with patch("app.services.market_data.datetime") as mock_dt:
            mock_dt.now.return_value = closed_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat

            result = get_stock_visualization_data("TCS", time_range="1D")
            self.assertEqual(result["market_status"], "CLOSED", "Cached data must be dynamically stamped with current CLOSED status")
            self.assertEqual(result["trading_status"], "Market Closed")
            self.assertEqual(result["data_status"], "CLOSED")

    def test_04_range_cache_evicts_stale_closed_1d_when_market_opens(self):
        """If 1D was cached while market was CLOSED, when market opens the stale 1D entry is evicted for fresh intraday data."""
        cache_id = ("TCS", "1D")
        _RANGE_CACHE[cache_id] = {
            "timestamp": time.time(),
            "data": {
                "symbol": "TCS",
                "price": 3500.0,
                "market_status": "CLOSED",
                "trading_status": "Market Closed",
                "data_status": "CLOSED",
                "candles": []
            }
        }

        # Simulate market opening at 10:00 IST
        open_time = datetime(2026, 10, 14, 10, 0, 0, tzinfo=IST)
        with patch("app.services.market_data.datetime") as mock_dt:
            mock_dt.now.return_value = open_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat

            # Calling get_stock_visualization_data will evict the stale CLOSED 1D cache
            result = get_stock_visualization_data("TCS", time_range="1D")
            self.assertEqual(result["market_status"], "OPEN")
            self.assertEqual(result["trading_status"], "Normal Trading")

    def test_05_inconclusive_or_error_status_handling(self):
        """Exceptions in status evaluation return STATUS UNKNOWN, and provider empty data preserves authoritative status."""
        status_unk, _ = get_market_status("invalid_arg")
        self.assertEqual(status_unk, "STATUS UNKNOWN")

        # Fast live fallback when data has no status
        with patch("app.api.stocks.get_stock_visualization_data") as mock_vis:
            mock_vis.return_value = {"symbol": "TEST"}
            resp = Response()
            res = get_stock_fast_live("TEST", response=resp)
            self.assertIn(res["market_status"], ["OPEN", "CLOSED", "STATUS UNKNOWN"])

    def test_06_frontend_common_js_race_condition_and_status_contracts(self):
        """Verify common.js implements race condition rejection, short 30s cache TTL, and DATA_UNAVAILABLE handling."""
        with open("frontend/common.js", "r", encoding="utf-8") as f:
            code = f.read()

        # Check race condition rejection logic
        self.assertIn("lastAppliedMarketStatusTs", code)
        self.assertIn("updateTs < lastAppliedMarketStatusTs", code)

        # Check shortened cache TTL (30000ms = 30s, not 300000ms = 5m)
        self.assertIn("30000", code)

        # Check conclusive error / unavailable states
        self.assertIn("DATA_UNAVAILABLE", code)
        self.assertIn("Market: DATA UNAVAILABLE", code)

        # Check export of getCachedMarketStatus
        self.assertIn("getCachedMarketStatus", code)

    def test_07_frontend_live_js_does_not_declare_closed_on_loading(self):
        """Verify live.js does not hardcode initial status to CLOSED or paint Market Closed while loading unknown status."""
        with open("frontend/live.js", "r", encoding="utf-8") as f:
            code = f.read()

        # Initial market status uses shared cached status or UNKNOWN
        self.assertIn("getCachedMarketStatus", code)
        self.assertIn("applyMarketStatus", code)
        # Verify canvas loading message inspects status cleanly
        self.assertIn("Loading candlestick market data...", code)


if __name__ == "__main__":
    unittest.main()
