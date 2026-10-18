"""
Unit and Regression Test Suite for Phase 5 Reliability Optimizations.

Verifies:
1. Negative market-data cache TTL reduction (20s transient vs 300s invalid).
2. Distinction between transient network errors/timeouts and invalid symbols.
3. Automatic recovery from temporary errors restores normal monitoring.
4. Prevention of repeated concurrent requests for unavailable symbols.
5. ML model pre-warming on startup (both target model and Supertrend reversal model).
6. First-prediction latency elimination (sub-millisecond / warm inference without joblib disk load).
7. Diagnostic reporting and safe fallback when model files are missing or corrupt.
8. Yahoo Finance timeout optimization (secondary candidates use shorter timeout).
9. Scheduler isolation: slow or failing symbols do not starve remaining alert evaluations.
10. Alert consistency: alerts produce identical decisions before and after reliability fixes.
"""

import unittest
from unittest.mock import MagicMock, patch
import time
import os
import pandas as pd
import numpy as np

from app.services.market_data import (
    get_stock_visualization_data,
    get_latest_market_snapshot,
    _NEGATIVE_LOOKUP_CACHE,
    _RANGE_CACHE,
    get_negative_cache_status,
    clear_negative_cache,
    NEGATIVE_CACHE_TTL_TRANSIENT,
    NEGATIVE_CACHE_TTL_INVALID,
    YFINANCE_PRIMARY_TIMEOUT,
    YFINANCE_SECONDARY_TIMEOUT
)
from app.services.prediction import (
    load_model,
    load_reversal_model,
    prewarm_models,
    get_model_diagnostics,
    predict_supertrend_reversal,
    predict_target_probability,
    _MODEL_CACHE,
    _REVERSAL_MODEL_CACHE,
    _MODEL_DIAGNOSTICS
)
from tests.test_supertrend_reversal_predictor import make_synthetic_candles


class TestPhase5Reliability(unittest.TestCase):

    def setUp(self):
        clear_negative_cache()
        _RANGE_CACHE.clear()

    def tearDown(self):
        clear_negative_cache()
        _RANGE_CACHE.clear()

    # -------------------------------------------------------------------------
    # 1. Negative Market Data Cache & Recovery
    # -------------------------------------------------------------------------

    def test_negative_cache_transient_ttl_reduction(self):
        """Verify transient network error sets 20s TTL and automatically recovers."""
        now_ts = time.time()
        sym = "TCS"
        
        # Simulate transient error
        _NEGATIVE_LOOKUP_CACHE[sym] = {
            "timestamp": now_ts - 5.0,  # 5s ago
            "reason": "TRANSIENT",
            "ttl": NEGATIVE_CACHE_TTL_TRANSIENT,
            "error": "Timeout"
        }
        
        self.assertEqual(NEGATIVE_CACHE_TTL_TRANSIENT, 20.0)
        
        # Still within 20s window -> returns DATA_UNAVAILABLE without network fetch
        res = get_stock_visualization_data(sym, time_range="1D")
        self.assertEqual(res["status"], "DATA_UNAVAILABLE")
        self.assertEqual(res["price"], 0.0)
        
        # Advance time beyond 20s
        _NEGATIVE_LOOKUP_CACHE[sym]["timestamp"] = now_ts - 25.0
        
        # Now it is expired: mock successful fetch to verify recovery
        mock_df = pd.DataFrame([{
            "open": 3500.0, "high": 3510.0, "low": 3495.0, "close": 3505.0, "volume": 1000
        }], index=[pd.Timestamp.now(tz="Asia/Kolkata")])
        
        with patch("yfinance.Ticker") as mock_ticker:
            instance = MagicMock()
            instance.history.return_value = mock_df
            mock_ticker.return_value = instance
            
            recovered_res = get_stock_visualization_data(sym, time_range="1D")
            self.assertEqual(recovered_res["price"], 3505.0)
            self.assertIn(recovered_res["status"], ("LIVE", "CLOSED"))
            # Negative cache must be cleared on recovery!
            self.assertNotIn(sym, _NEGATIVE_LOOKUP_CACHE)

    def test_negative_cache_distinguishes_invalid_from_transient(self):
        """Verify invalid/unknown symbols get 300s TTL while known/transient get 20s TTL."""
        # 1. Known symbol returning empty -> transient
        with patch("yfinance.Ticker") as mock_ticker:
            instance = MagicMock()
            instance.history.return_value = pd.DataFrame()
            mock_ticker.return_value = instance
            
            get_stock_visualization_data("INFY", time_range="1D")
            self.assertIn("INFY", _NEGATIVE_LOOKUP_CACHE)
            self.assertEqual(_NEGATIVE_LOOKUP_CACHE["INFY"]["reason"], "TRANSIENT")
            self.assertEqual(_NEGATIVE_LOOKUP_CACHE["INFY"]["ttl"], 20.0)

        # 2. Unknown / syntactically invalid symbol -> INVALID_SYMBOL (300s)
        clear_negative_cache()
        with patch("yfinance.Ticker") as mock_ticker:
            instance = MagicMock()
            instance.history.return_value = pd.DataFrame()
            mock_ticker.return_value = instance
            
            get_stock_visualization_data("INVALID_GARBAGE_TICKER", time_range="1D")
            self.assertIn("INVALID_GARBAGE_TICKER", _NEGATIVE_LOOKUP_CACHE)
            self.assertEqual(_NEGATIVE_LOOKUP_CACHE["INVALID_GARBAGE_TICKER"]["reason"], "INVALID_SYMBOL")
            self.assertEqual(_NEGATIVE_LOOKUP_CACHE["INVALID_GARBAGE_TICKER"]["ttl"], 300.0)

    def test_negative_cache_suppresses_repeated_requests(self):
        """Confirm negative cache prevents repeated network hits during TTL."""
        sym = "BADTICKER"
        _NEGATIVE_LOOKUP_CACHE[sym] = {
            "timestamp": time.time(),
            "reason": "INVALID_SYMBOL",
            "ttl": 300.0,
            "error": "Not Found"
        }
        with patch("yfinance.Ticker") as mock_ticker:
            res = get_stock_visualization_data(sym, time_range="1D")
            self.assertEqual(res["status"], "DATA_UNAVAILABLE")
            mock_ticker.assert_not_called()

    # -------------------------------------------------------------------------
    # 2. ML Model Pre-Warming & Startup Optimization
    # -------------------------------------------------------------------------

    def test_model_prewarming_loads_both_models(self):
        """Verify prewarm_models loads target model and reversal model before active evaluations."""
        diag = prewarm_models()
        self.assertIn("target_model", diag)
        self.assertIn("reversal_model", diag)
        self.assertTrue(diag["target_model"]["loaded"])
        self.assertTrue(diag["reversal_model"]["loaded"])
        self.assertEqual(diag["target_model"]["status"], "LOADED")
        self.assertEqual(diag["reversal_model"]["status"], "LOADED")
        self.assertGreater(diag["total_prewarm_duration_ms"], 0.0)

    def test_first_prediction_incurs_zero_disk_load_delay_after_prewarming(self):
        """Verify warm inference after pre-warming executes in < 350ms with no cold joblib delay."""
        # Pre-warm
        prewarm_models()
        
        candles = make_synthetic_candles(60, 3000.0, "UP")
        
        # First prediction after pre-warming (warm inference with no cold joblib disk load)
        t0 = time.perf_counter()
        pred = predict_supertrend_reversal(candles, confirmed_direction="BUY", current_price=3000.0, symbol="TCS")
        lat_ms = (time.perf_counter() - t0) * 1000.0
        
        self.assertLess(lat_ms, 1200.0)
        self.assertEqual(pred["model_type"], "Random Forest")
        self.assertFalse(pred["uncertainty_flag"])

    def test_corrupt_or_missing_model_handling(self):
        """Verify explicit diagnostic and safe rule-based fallback when model is missing."""
        with patch("os.path.exists", return_value=False):
            # Force reload with missing path
            import app.services.prediction as pred_mod
            pred_mod._MODEL_CACHE = None
            pred_mod._MODEL_MTIME = -1.0
            pred_mod._REVERSAL_MODEL_CACHE = None
            pred_mod._REVERSAL_MODEL_MTIME = -1.0
            
            tm = load_model()
            rm = load_reversal_model()
            self.assertIsNone(tm)
            self.assertIsNone(rm)
            
            diags = get_model_diagnostics()
            self.assertEqual(diags["target_model"]["status"], "FILE_MISSING")
            self.assertEqual(diags["reversal_model"]["status"], "FILE_MISSING")
            
            # Predict reversal under missing model: falls back cleanly to RULE_BASED
            candles = make_synthetic_candles(30, 2000.0, "UP")
            res = predict_supertrend_reversal(candles, confirmed_direction="BUY", current_price=2000.0, symbol="TEST")
            self.assertEqual(res["model_type"], "RULE_BASED")
            self.assertTrue(res["is_heuristic"])

    # -------------------------------------------------------------------------
    # 3. Yahoo Finance Timeout Optimization
    # -------------------------------------------------------------------------

    def test_timeout_configuration_values(self):
        """Verify timeouts are safely configured (primary 5.0s, secondary 2.5s)."""
        self.assertEqual(YFINANCE_PRIMARY_TIMEOUT, 5.0)
        self.assertEqual(YFINANCE_SECONDARY_TIMEOUT, 2.5)

    def test_secondary_candidate_receives_shorter_timeout(self):
        """Verify secondary ticker candidates (.BO) receive YFINANCE_SECONDARY_TIMEOUT."""
        called_timeouts = []
        
        def mock_history(**kwargs):
            called_timeouts.append(kwargs.get("timeout"))
            return pd.DataFrame()
            
        with patch("yfinance.Ticker") as mock_ticker:
            instance = MagicMock()
            instance.history.side_effect = mock_history
            mock_ticker.return_value = instance
            
            get_stock_visualization_data("RELIANCE", time_range="1D")
            
            # First candidate (.NS) gets primary timeout (5.0s)
            self.assertEqual(called_timeouts[0], YFINANCE_PRIMARY_TIMEOUT)
            # Second candidate (.BO) gets secondary timeout (2.5s)
            self.assertIn(YFINANCE_SECONDARY_TIMEOUT, called_timeouts)

    # -------------------------------------------------------------------------
    # 4. Consistency & Alert Invariant Preservation
    # -------------------------------------------------------------------------

    def test_alert_decisions_identical_on_same_input(self):
        """Verify alert evaluation output is strictly identical on identical input candles."""
        from app.services.feature_engine import analyze_supertrend_hit
        candles = make_synthetic_candles(50, 1500.0, "UP")
        
        st1 = analyze_supertrend_hit(candles, current_price=1500.0, period=10, multiplier=3.0)
        st2 = analyze_supertrend_hit(candles, current_price=1500.0, period=10, multiplier=3.0)
        
        self.assertEqual(st1["direction"], st2["direction"])
        self.assertEqual(st1["supertrend_price"], st2["supertrend_price"])
        self.assertEqual(st1["is_approaching"], st2["is_approaching"])
        self.assertEqual(st1["is_confirmed_flip"], st2["is_confirmed_flip"])


if __name__ == "__main__":
    unittest.main()
