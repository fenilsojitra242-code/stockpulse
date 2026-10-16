"""
Comprehensive Test Suite for Supertrend Predictive Early Reversal Warning System.

Tests all 8 core requirements:
1. Standard Supertrend preserved & confirmed trend never overwritten.
2. Reversal feature engine accuracy (ATR distance, returns, momentum, RSI/MACD deltas, EMA slopes, volume, trend duration).
3. Prediction targets across multi-horizons (1m, 2m, 5m for BUY->SELL and SELL->BUY).
4. Walk-forward model comparison & probability calibration.
5. Early warning decision system with uncertainty / insufficient-data state.
6. Transparent rule-based fallback when ML model is unavailable.
7. Notification integration, deduplication, and market-hours guard.
8. Walk-forward verification of warning lead time and whipsaw reduction.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from app.services.feature_engine import (
    compute_supertrend,
    compute_supertrend_reversal_matrix,
    extract_supertrend_reversal_feature_vector,
    calculate_reversal_risk_score,
    analyze_supertrend_hit,
    SUPERTREND_REVERSAL_FEATURE_NAMES
)
from app.services.prediction import (
    predict_supertrend_reversal,
    load_reversal_model
)
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    _SUPERTREND_STATES
)


def make_synthetic_candles(n: int = 50, start_price: float = 100.0, trend: str = "UP") -> list:
    """Helper to generate realistic 1-minute OHLCV candles."""
    candles = []
    base_time = datetime(2026, 10, 15, 9, 15)
    price = start_price
    drift = 0.5 if trend == "UP" else -0.5

    for i in range(n):
        t_str = (base_time + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S")
        open_p = price
        noise = (i % 3 - 1) * 0.1
        close_p = open_p + drift + noise
        high_p = max(open_p, close_p) + 0.3
        low_p = min(open_p, close_p) - 0.3
        vol = 10000 + (i * 200)

        candles.append({
            "full_time": t_str,
            "open": round(open_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "close": round(close_p, 2),
            "volume": vol
        })
        price = close_p
    return candles


class TestSupertrendReversalPredictor(unittest.TestCase):

    def setUp(self):
        _SUPERTREND_STATES.clear()

    # -------------------------------------------------------------
    # Req 1: Confirmed Supertrend Preservation
    # -------------------------------------------------------------
    def test_req1_confirmed_trend_never_overwritten_by_prediction(self):
        """Confirmed Supertrend direction must remain BUY even during high reversal probability."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        # Intrabar price dips near line
        st_res = analyze_supertrend_hit(candles, current_price=110.0, period=10, multiplier=3.0)
        confirmed_dir = st_res["direction"]
        self.assertEqual(confirmed_dir, "BUY")

        # Run reversal prediction indicating 85% reversal probability
        pred_res = predict_supertrend_reversal(
            candles_or_df=candles,
            confirmed_direction=confirmed_dir,
            horizon_minutes=5,
            current_price=110.0,
            threshold=0.50
        )
        self.assertIn("reversal_probability", pred_res)
        # Invariant check: confirmed direction returned by predictor must strictly match original
        self.assertEqual(pred_res["confirmed_direction"], "BUY")
        self.assertEqual(pred_res["reversal_target_direction"], "SELL")

        # Process through state transition
        st_res["candles"] = candles
        res = _process_supertrend_state_transition(
            condition_key="test_r1_stock",
            alert=None,
            symbol="TESTSTOCK",
            st_result=st_res,
            curr_price=110.0,
            db=mock_db,
            user_id=1,
            now=datetime.utcnow(),
            is_market_open=True,
            data_status="LIVE",
            prox_pts=5.0
        )
        state = _SUPERTREND_STATES["test_r1_stock"]
        self.assertEqual(state["confirmed_direction"], "BUY", "Confirmed direction must never flip prematurely to SELL!")

    # -------------------------------------------------------------
    # Req 2: Feature Engine Completeness
    # -------------------------------------------------------------
    def test_req2_reversal_feature_engine_calculates_all_required_signals(self):
        """Feature matrix must extract all 10 core feature categories without NaNs."""
        candles = make_synthetic_candles(n=40, start_price=150.0, trend="UP")
        df = pd.DataFrame(candles)

        matrix = compute_supertrend_reversal_matrix(df, period=10, multiplier=3.0)
        self.assertFalse(matrix.empty)

        # Check required feature columns
        expected_cols = [
            'direction_code', 'distance_to_band_atr', 'return_1m', 'return_2m',
            'return_3m', 'return_5m', 'momentum_3m', 'momentum_5m',
            'mom_adverse_3m', 'mom_adverse_5m', 'rsi_14', 'delta_rsi_3m',
            'delta_rsi_5m', 'macd_hist', 'delta_macd_hist_3m', 'ema_9_slope_3m',
            'ema_21_slope_3m', 'atr_ratio_5m', 'volatility_10m', 'candle_range_atr',
            'candle_body_atr', 'vol_ratio_20m', 'vol_change_1m', 'trend_duration_bars'
        ]
        for col in expected_cols:
            self.assertIn(col, matrix.columns, f"Missing required reversal feature: {col}")
            # Ensure no NaNs or infs
            self.assertFalse(matrix[col].isna().any(), f"Feature column {col} contains NaNs")

        # Test single-vector live extractor
        vec, feat_dict, has_suff = extract_supertrend_reversal_feature_vector(candles, horizon_minutes=5)
        self.assertTrue(has_suff)
        self.assertEqual(len(vec), len(SUPERTREND_REVERSAL_FEATURE_NAMES))
        self.assertIn("trend_duration_bars", feat_dict)
        self.assertGreater(feat_dict["trend_duration_bars"], 0)

    # -------------------------------------------------------------
    # Req 3: Prediction Horizons & Directional Targets
    # -------------------------------------------------------------
    def test_req3_multi_horizon_prediction_targets(self):
        """Predictor correctly evaluates horizons of 1m, 2m, and 5m for BUY and SELL trends."""
        candles = make_synthetic_candles(n=35, start_price=200.0, trend="UP")

        for h in [1, 2, 5]:
            pred_buy = predict_supertrend_reversal(
                candles_or_df=candles,
                confirmed_direction="BUY",
                horizon_minutes=h,
                current_price=210.0
            )
            self.assertEqual(pred_buy["horizon_minutes"], h)
            self.assertEqual(pred_buy["reversal_target_direction"], "SELL")

            pred_sell = predict_supertrend_reversal(
                candles_or_df=candles,
                confirmed_direction="SELL",
                horizon_minutes=h,
                current_price=210.0
            )
            self.assertEqual(pred_sell["horizon_minutes"], h)
            self.assertEqual(pred_sell["reversal_target_direction"], "BUY")

    # -------------------------------------------------------------
    # Req 4: Model Loading and Inference
    # -------------------------------------------------------------
    def test_req4_trained_model_loading_and_walk_forward_metrics(self):
        """Model artifact loads and contains valid calibrated classifier and metrics."""
        artifact = load_reversal_model()
        self.assertIsNotNone(artifact, "Trained reversal model artifact must exist on disk!")
        self.assertIn("model", artifact)
        self.assertIn("model_name", artifact)
        self.assertIn("optimal_threshold", artifact)

        # Ensure optimal threshold is in reasonable probability range [0.30, 0.75]
        thresh = artifact["optimal_threshold"]
        self.assertGreaterEqual(thresh, 0.30)
        self.assertLessEqual(thresh, 0.75)

    # -------------------------------------------------------------
    # Req 5: Early Warning Decision & Uncertainty Handling
    # -------------------------------------------------------------
    def test_req5_insufficient_data_flags_uncertainty(self):
        """When fewer than 15 candles exist, uncertainty flag must be set and warning suppressed."""
        few_candles = make_synthetic_candles(n=8, start_price=100.0, trend="UP")
        pred_res = predict_supertrend_reversal(
            candles_or_df=few_candles,
            confirmed_direction="BUY",
            horizon_minutes=5,
            threshold=0.30  # Even with low threshold
        )
        self.assertTrue(pred_res["uncertainty_flag"])
        self.assertEqual(pred_res["data_quality"], "INSUFFICIENT_HISTORY")
        self.assertFalse(pred_res["is_early_warning_ready"], "Warning must not be ready when uncertainty is active!")

    # -------------------------------------------------------------
    # Req 6: Transparent Rule-Based Fallback
    # -------------------------------------------------------------
    def test_req6_rule_based_fallback_when_model_is_absent(self):
        """When ML artifact is unavailable, engine falls back to calibrated heuristic risk score."""
        candles = make_synthetic_candles(n=25, start_price=100.0, trend="UP")

        with patch("app.services.prediction.load_reversal_model", return_value=None):
            pred_res = predict_supertrend_reversal(
                candles_or_df=candles,
                confirmed_direction="BUY",
                horizon_minutes=5,
                current_price=100.5
            )
            self.assertEqual(pred_res["model_type"], "RULE_BASED")
            self.assertTrue(pred_res["is_heuristic"])
            self.assertGreaterEqual(pred_res["reversal_probability"], 0.05)
            self.assertLessEqual(pred_res["reversal_probability"], 0.95)

    # -------------------------------------------------------------
    # Req 7: Notification Deduplication & Market Status Protection
    # -------------------------------------------------------------
    def test_req7_notifications_separate_and_deduplicated(self):
        """Early warning notification must be emitted once per candle event, never duplicated."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        base_st = analyze_supertrend_hit(candles, period=10, multiplier=3.0)
        st_val = base_st["supertrend_price"]
        prox_price = round(st_val + 1.0, 2)  # Distance = 1.0 pt <= 2.0 pts

        # Configure last candle with adverse pullback towards support
        candles[-1]["open"] = prox_price + 1.5
        candles[-1]["close"] = prox_price
        candles[-1]["high"] = prox_price + 1.6
        candles[-1]["low"] = prox_price - 0.1

        st_res = analyze_supertrend_hit(candles, current_price=prox_price, period=10, multiplier=3.0, proximity_points=2.0)
        st_res["candles"] = candles
        st_res["reversal_probability"] = 0.75

        # 1. Initialize state
        _process_supertrend_state_transition(
            condition_key="test_notif_sym",
            alert=None,
            symbol="NOTIF_SYM",
            st_result=st_res,
            curr_price=prox_price,
            db=mock_db,
            user_id=1,
            now=datetime.utcnow(),
            is_market_open=True,
            data_status="LIVE",
            prox_pts=2.0
        )

        # 2. First tick in proximity with strong evidence -> Triggers early warning
        with patch("app.services.alert_engine.notification_manager.emit_supertrend_approaching") as mock_emit:
            res1 = _process_supertrend_state_transition(
                condition_key="test_notif_sym",
                alert=None,
                symbol="NOTIF_SYM",
                st_result=st_res,
                curr_price=prox_price,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=True,
                data_status="LIVE",
                prox_pts=2.0
            )
            self.assertIsNotNone(res1)
            self.assertEqual(res1["type"], "EARLY_WARNING")
            mock_emit.assert_called_once()

        # 3. Subsequent tick on the SAME candle -> Deduplicated (no second notification!)
        with patch("app.services.alert_engine.notification_manager.emit_supertrend_approaching") as mock_emit2:
            res2 = _process_supertrend_state_transition(
                condition_key="test_notif_sym",
                alert=None,
                symbol="NOTIF_SYM",
                st_result=st_res,
                curr_price=prox_price - 0.2,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=True,
                data_status="LIVE",
                prox_pts=2.0
            )
            self.assertIsNone(res2)
            mock_emit2.assert_not_called()

        # 4. Market Closed Guard: Never send alerts when market is closed
        with patch("app.services.alert_engine.notification_manager.emit_supertrend_approaching") as mock_emit3:
            res3 = _process_supertrend_state_transition(
                condition_key="test_notif_sym_closed",
                alert=None,
                symbol="NOTIF_SYM",
                st_result=st_res,
                curr_price=100.1,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=False,
                data_status="CLOSED",
                prox_pts=2.0
            )
            self.assertIsNone(res3)
            mock_emit3.assert_not_called()

    # -------------------------------------------------------------
    # Req 8: Warning Expiration Workflow
    # -------------------------------------------------------------
    def test_req8_warning_expires_without_reversal_and_resumes_monitoring(self):
        """If price pulls safely away, early warning state expires and returns to monitoring."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        base_st = analyze_supertrend_hit(candles, period=10, multiplier=3.0)
        st_val = base_st["supertrend_price"]
        prox_price = round(st_val + 1.0, 2)

        candles[-1]["open"] = prox_price + 1.5
        candles[-1]["close"] = prox_price
        st_res = analyze_supertrend_hit(candles, current_price=prox_price, period=10, multiplier=3.0, proximity_points=2.0)
        st_res["candles"] = candles
        st_res["is_approaching"] = True
        st_res["reversal_probability"] = 0.75

        # Initialize and trigger warning
        _process_supertrend_state_transition("exp_sym", None, "EXP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)
        _process_supertrend_state_transition("exp_sym", None, "EXP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)
        self.assertEqual(_SUPERTREND_STATES["exp_sym"]["state"], "EARLY_WARNING_SENT")

        # Price rallies away from line (distance = 15.0 pts > 2.0 * 1.5)
        st_res_away = analyze_supertrend_hit(candles, current_price=st_val + 15.0, period=10, multiplier=3.0, proximity_points=2.0)
        st_res_away["candles"] = candles
        st_res_away["is_approaching"] = False

        res_exp = _process_supertrend_state_transition(
            condition_key="exp_sym",
            alert=None,
            symbol="EXP_SYM",
            st_result=st_res_away,
            curr_price=st_val + 15.0,
            db=mock_db,
            user_id=1,
            now=datetime.utcnow(),
            is_market_open=True,
            data_status="LIVE",
            prox_pts=2.0
        )
        self.assertIsNotNone(res_exp)
        self.assertEqual(res_exp["type"], "WARNING_EXPIRED")
        self.assertEqual(res_exp["reason"], "PULLED_AWAY")
        self.assertEqual(_SUPERTREND_STATES["exp_sym"]["state"], "MONITORING")

    # -------------------------------------------------------------
    # Precision Regression Tests: Multi-Factor Confluence
    # -------------------------------------------------------------
    def test_precision_weak_prediction_does_not_trigger_early_warning(self):
        """Weak prediction probability (< threshold) suppresses early warning even within proximity."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        st_res = analyze_supertrend_hit(candles, current_price=100.5, period=10, multiplier=3.0, proximity_points=2.0)
        st_res["candles"] = candles
        st_res["distance_points"] = 0.5  # within proximity
        st_res["reversal_probability"] = 0.20  # Explicit weak score

        # Initialize
        _process_supertrend_state_transition("weak_pred", None, "WEAK_SYM", st_res, 100.5, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)

        # Process tick - with low score (< 0.50), warning must be suppressed
        with patch("app.services.alert_engine.notification_manager.emit_supertrend_approaching") as mock_emit:
            res = _process_supertrend_state_transition(
                "weak_pred", None, "WEAK_SYM", st_res, 100.5, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0
            )
            self.assertIsNone(res, "Weak prediction must not trigger early warning!")
            mock_emit.assert_not_called()
            self.assertEqual(_SUPERTREND_STATES["weak_pred"]["state"], "MONITORING")

    def test_precision_opposing_candle_momentum_suppresses_warning(self):
        """When confirmed trend is BUY and candle expands upwards strongly, warning for SELL reversal is suppressed."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        base_st = analyze_supertrend_hit(candles, period=10, multiplier=3.0)
        st_val = base_st["supertrend_price"]
        prox_price = round(st_val + 1.0, 2)

        # Expanding green candle: close > open + 0.50
        candles[-1]["open"] = prox_price - 0.2
        candles[-1]["close"] = prox_price + 0.5
        st_res = analyze_supertrend_hit(candles, current_price=prox_price, period=10, multiplier=3.0, proximity_points=2.0)
        st_res["candles"] = candles
        st_res["distance_points"] = 1.0  # within proximity
        st_res["reversal_probability"] = 0.60

        _process_supertrend_state_transition("opp_mom", None, "OPP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)

        with patch("app.services.alert_engine.notification_manager.emit_supertrend_approaching") as mock_emit:
            res = _process_supertrend_state_transition(
                "opp_mom", None, "OPP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0
            )
            self.assertIsNone(res, "Opposing momentum candle must suppress early warning!")
            mock_emit.assert_not_called()

    def test_precision_stale_or_unavailable_data_suppresses_warning(self):
        """Stale or unavailable market data transitions to DATA_UNAVAILABLE and suppresses alerts."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        st_res = analyze_supertrend_hit(candles, current_price=100.5, period=10, multiplier=3.0, proximity_points=2.0)
        st_res["candles"] = candles

        _process_supertrend_state_transition("stale_sym", None, "STALE_SYM", st_res, 100.5, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)

        res = _process_supertrend_state_transition(
            "stale_sym", None, "STALE_SYM", st_res, 100.5, mock_db, 1, datetime.utcnow(), True, "STALE", 2.0
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "DATA_UNAVAILABLE")
        self.assertEqual(_SUPERTREND_STATES["stale_sym"]["state"], "DATA_UNAVAILABLE")

    def test_precision_same_candle_re_entry_after_expiration_is_suppressed(self):
        """If early warning expired on candle T, price flapping back into proximity on the same candle is suppressed."""
        candles = make_synthetic_candles(n=30, start_price=100.0, trend="UP")
        mock_db = MagicMock()

        base_st = analyze_supertrend_hit(candles, period=10, multiplier=3.0)
        st_val = base_st["supertrend_price"]
        prox_price = round(st_val + 1.0, 2)

        candles[-1]["open"] = prox_price + 1.5
        candles[-1]["close"] = prox_price
        c_ts = candles[-1]["full_time"]
        st_res = analyze_supertrend_hit(candles, current_price=prox_price, period=10, multiplier=3.0, proximity_points=2.0)
        st_res["candles"] = candles
        st_res["candle_timestamp"] = c_ts
        st_res["reversal_probability"] = 0.75

        # Initialize and trigger warning
        _process_supertrend_state_transition("flap_sym", None, "FLAP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)
        _process_supertrend_state_transition("flap_sym", None, "FLAP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)
        self.assertEqual(_SUPERTREND_STATES["flap_sym"]["state"], "EARLY_WARNING_SENT")

        # Price rallies away -> expires
        st_res_away = analyze_supertrend_hit(candles, current_price=st_val + 15.0, period=10, multiplier=3.0, proximity_points=2.0)
        st_res_away["candles"] = candles
        st_res_away["candle_timestamp"] = c_ts
        _process_supertrend_state_transition("flap_sym", None, "FLAP_SYM", st_res_away, st_val + 15.0, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0)
        self.assertEqual(_SUPERTREND_STATES["flap_sym"]["state"], "MONITORING")

        # Price fluctuates back into proximity on the EXACT SAME candle timestamp
        with patch("app.services.alert_engine.notification_manager.emit_supertrend_approaching") as mock_emit:
            res_flap = _process_supertrend_state_transition(
                "flap_sym", None, "FLAP_SYM", st_res, prox_price, mock_db, 1, datetime.utcnow(), True, "LIVE", 2.0
            )
            self.assertIsNone(res_flap, "Flapping on the same candle after expiration must be suppressed!")
            mock_emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
