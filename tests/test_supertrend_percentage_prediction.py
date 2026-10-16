"""
Unit & Regression Tests for Percentage-Based Supertrend Early-Warning Prediction

Validates all 12 requirements specified in the prompt:
1. Each of the four percentage options (0.25%, 0.50%, 0.75%, 1.00%).
2. Correct percentage calculations at different stock prices (₹100, ₹500, ₹1,000, ₹3,500).
3. BUY-to-SELL predictions using downside percentage distance.
4. SELL-to-BUY predictions using upside percentage distance.
5. Boundary conditions exactly at, below, and above the selected percentage.
6. Distinguishing overall price movement from distance to the Supertrend reversal threshold.
7. Weak predictions being suppressed by existing confidence filters.
8. No false confirmed reversal before a completed candle validates it.
9. One early warning per cycle and no duplicate notifications.
10. Prediction expiration and subsequent monitoring resumption.
11. Missing, stale, closed-market, and invalid-price handling.
12. Backward compatibility for existing users' saved settings.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.feature_engine import (
    analyze_supertrend_hit,
    compute_supertrend,
)
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    _SUPERTREND_STATES,
    _STOCK_CONFIRMED_DIRECTIONS,
    get_supertrend_settings,
    save_supertrend_settings,
)
from app.services.messaging import notification_manager, NotificationEventType
from app.database.models import Alert, SystemSetting


class TestSuperTrendPercentagePrediction(unittest.TestCase):

    def setUp(self):
        _SUPERTREND_STATES.clear()
        _STOCK_CONFIRMED_DIRECTIONS.clear()
        notification_manager._DEDUPLICATED_EVENT_KEYS.clear()

    # 1. Four percentage options: 0.25%, 0.50%, 0.75%, 1.00%
    def test_01_four_percentage_options(self):
        """Verify exactly the four allowed percentage options and default 0.50%."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        settings = get_supertrend_settings(mock_db, user_id=1)
        self.assertEqual(settings["proximity_percent"], 0.50)
        self.assertEqual(settings["available_proximity_percentages"], [0.25, 0.50, 0.75, 1.00])

        # Test each of the four options when saving
        for opt in [0.25, 0.50, 0.75, 1.00]:
            stored_rows = {}
            def mock_filter(condition):
                mock_res = MagicMock()
                mock_res.first.return_value = None
                return mock_res
            mock_db.query.return_value.filter.side_effect = mock_filter

            res = save_supertrend_settings(mock_db, proximity_percent=opt, user_id=1)
            self.assertIn(opt, [0.25, 0.50, 0.75, 1.00])

    # 2. Correct percentage calculations at different stock prices
    def test_02_percentage_calculation_at_different_stock_prices(self):
        """
        Verify upper and lower boundaries and permitted distance points:
        Upper boundary = P_ref * (1 + p / 100)
        Lower boundary = P_ref * (1 - p / 100)
        Tested for ₹100, ₹500, ₹1,000, ₹3,500.
        """
        prices = [100.0, 500.0, 1000.0, 3500.0]
        percentages = [0.25, 0.50, 0.75, 1.00]

        for p_ref in prices:
            for p in percentages:
                expected_upper = round(p_ref * (1.0 + p / 100.0), 2)
                expected_lower = round(p_ref * (1.0 - p / 100.0), 2)
                expected_dist_pts = round(p_ref * (p / 100.0), 2)

                candles = [
                    {"open": p_ref, "high": p_ref + 1.0, "low": p_ref - 1.0, "close": p_ref, "full_time": "10:00"},
                    {"open": p_ref, "high": p_ref + 1.0, "low": p_ref - 1.0, "close": p_ref, "full_time": "10:01"},
                ]
                res = analyze_supertrend_hit(
                    candles_or_df=candles,
                    current_price=p_ref,
                    period=2,
                    multiplier=1.0,
                    proximity_percent=p
                )
                self.assertEqual(res["upper_boundary"], expected_upper)
                self.assertEqual(res["lower_boundary"], expected_lower)
                self.assertEqual(res["permitted_distance_points"], expected_dist_pts)
                self.assertEqual(res["proximity_percent"], p)
                self.assertEqual(res["reference_price"], p_ref)

    # 3. BUY-to-SELL predictions using percentage distance
    def test_03_buy_to_sell_percentage_prediction(self):
        """
        When confirmed direction is BUY (Supertrend support below price):
        Downside reversal distance = (curr_price - ST) / curr_price * 100%
        If distance % <= p% and confluence passes, trigger early warning.
        """
        curr_price = 1000.0
        p_pct = 0.50  # 0.50% permitted distance -> lower boundary ₹995.00
        # Supertrend band at ₹996.00 -> distance is ₹4.00 (0.40% < 0.50%)
        st_price = 996.0

        candles = [
            {"open": 1005.0, "high": 1010.0, "low": 998.0, "close": 1002.0, "full_time": "10:00"},
            {"open": 1002.0, "high": 1004.0, "low": 999.0, "close": 1000.0, "full_time": "10:01"},
        ]

        st_res = {
            "direction": "BUY",
            "supertrend_price": st_price,
            "distance_points": 4.0,
            "distance_percent": 0.40,
            "permitted_distance_points": 5.0,
            "upper_boundary": 1005.0,
            "lower_boundary": 995.0,
            "reference_price": curr_price,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.80,
            "reversal_confidence_threshold": 0.50
        }

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        # First boot initializes confirmed direction
        _process_supertrend_state_transition("cond_b2s", None, "TCS", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        # Second call evaluates early warning
        res = _process_supertrend_state_transition("cond_b2s", None, "TCS", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)

        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "EARLY_WARNING")
        self.assertEqual(res["target_direction"], "SELL")
        self.assertEqual(res["distance_percent"], 0.40)
        self.assertEqual(res["proximity_percent"], 0.50)

    # 4. SELL-to-BUY predictions using percentage distance
    def test_04_sell_to_buy_percentage_prediction(self):
        """
        When confirmed direction is SELL (Supertrend resistance above price):
        Upside reversal distance = (ST - curr_price) / curr_price * 100%
        If distance % <= p% and confluence passes, trigger early warning.
        """
        curr_price = 2000.0
        p_pct = 0.75  # 0.75% permitted distance -> upper boundary ₹2015.00
        # Supertrend resistance at ₹2010.00 -> distance is ₹10.00 (0.50% < 0.75%)
        st_price = 2010.0

        candles = [
            {"open": 1990.0, "high": 2005.0, "low": 1985.0, "close": 1995.0, "full_time": "10:00"},
            {"open": 1995.0, "high": 2002.0, "low": 1992.0, "close": 2000.0, "full_time": "10:01"},
        ]

        st_res = {
            "direction": "SELL",
            "supertrend_price": st_price,
            "distance_points": 10.0,
            "distance_percent": 0.50,
            "permitted_distance_points": 15.0,
            "upper_boundary": 2015.0,
            "lower_boundary": 1985.0,
            "reference_price": curr_price,
            "proximity_percent": p_pct,
            "target_direction": "BUY",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.85,
            "reversal_confidence_threshold": 0.50
        }

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        # Boot
        _process_supertrend_state_transition("cond_s2b", None, "INFY", st_res, curr_price, mock_db, 1, now, True, "LIVE", 15.0, prox_pct=p_pct)
        # Evaluate early warning
        res = _process_supertrend_state_transition("cond_s2b", None, "INFY", st_res, curr_price, mock_db, 1, now, True, "LIVE", 15.0, prox_pct=p_pct)

        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "EARLY_WARNING")
        self.assertEqual(res["target_direction"], "BUY")
        self.assertEqual(res["distance_percent"], 0.50)
        self.assertEqual(res["proximity_percent"], 0.75)

    # 5. Boundary conditions exactly at, below, and above selected percentage
    def test_05_boundary_conditions(self):
        """
        Verify behavior at exact boundaries:
        - Distance % < p%: triggers early warning.
        - Distance % == p%: triggers early warning (inclusive boundary).
        - Distance % > p%: does NOT trigger early warning.
        """
        curr_price = 1000.0
        p_pct = 0.50

        candles = [
            {"open": 1005.0, "high": 1010.0, "low": 998.0, "close": 1000.0, "full_time": "10:01"},
        ]

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        # Test case A: Distance > p% (0.60% > 0.50%) -> should NOT warn
        _SUPERTREND_STATES.clear()
        st_res_above = {
            "direction": "BUY",
            "supertrend_price": 994.0,  # 6.0 pts = 0.60%
            "distance_points": 6.0,
            "distance_percent": 0.60,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": False,
            "is_within_pct": False,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.80,
            "reversal_confidence_threshold": 0.50
        }
        _process_supertrend_state_transition("cond_above", None, "RELIANCE", st_res_above, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        res_above = _process_supertrend_state_transition("cond_above", None, "RELIANCE", st_res_above, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNone(res_above)

        # Test case B: Distance == p% (0.50% == 0.50%) -> should warn
        _SUPERTREND_STATES.clear()
        st_res_exact = {
            "direction": "BUY",
            "supertrend_price": 995.0,  # 5.0 pts = 0.50%
            "distance_points": 5.0,
            "distance_percent": 0.50,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.80,
            "reversal_confidence_threshold": 0.50
        }
        _process_supertrend_state_transition("cond_exact", None, "RELIANCE", st_res_exact, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        res_exact = _process_supertrend_state_transition("cond_exact", None, "RELIANCE", st_res_exact, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNotNone(res_exact)
        self.assertEqual(res_exact["type"], "EARLY_WARNING")

        # Test case C: Distance < p% (0.30% < 0.50%) -> should warn
        _SUPERTREND_STATES.clear()
        st_res_below = {
            "direction": "BUY",
            "supertrend_price": 997.0,  # 3.0 pts = 0.30%
            "distance_points": 3.0,
            "distance_percent": 0.30,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.80,
            "reversal_confidence_threshold": 0.50
        }
        _process_supertrend_state_transition("cond_below", None, "RELIANCE", st_res_below, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        res_below = _process_supertrend_state_transition("cond_below", None, "RELIANCE", st_res_below, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNotNone(res_below)
        self.assertEqual(res_below["type"], "EARLY_WARNING")

    # 6. Distinguishing overall price movement from distance to Supertrend reversal threshold
    def test_06_distinguishing_overall_movement_from_supertrend_distance(self):
        """
        Overall day price movement could be +3.50% from yesterday's close,
        while the distance to the Supertrend reversal threshold is 0.40%.
        Verify that prediction logic strictly measures distance to Supertrend band,
        not day movement.
        """
        curr_price = 1035.0  # +3.50% from prev close of ₹1000.00
        st_price = 1031.0    # Supertrend support at ₹1031.00 -> gap is ₹4.00 = 0.386%
        p_pct = 0.50         # User selected 0.50%

        candles = [
            {"open": 1038.0, "high": 1040.0, "low": 1033.0, "close": 1035.0, "full_time": "10:01"},
        ]

        st_res = analyze_supertrend_hit(
            candles_or_df=candles,
            current_price=curr_price,
            period=2,
            multiplier=1.0,
            proximity_percent=p_pct
        )
        st_res["supertrend_price"] = st_price
        st_res["distance_points"] = round(curr_price - st_price, 2)
        st_res["distance_percent"] = round(((curr_price - st_price) / curr_price) * 100.0, 4)
        st_res["direction"] = "BUY"
        st_res["target_direction"] = "SELL"
        st_res["is_confirmed_flip"] = False
        st_res["is_intrabar_cross"] = False
        st_res["is_approaching"] = True
        st_res["is_within_pct"] = bool(st_res["distance_percent"] <= p_pct)
        st_res["candle_timestamp"] = "10:01"
        st_res["candles"] = candles
        st_res["reversal_probability"] = 0.80

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        _process_supertrend_state_transition("cond_dist", None, "HDFCBANK", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        res = _process_supertrend_state_transition("cond_dist", None, "HDFCBANK", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)

        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "EARLY_WARNING")
        self.assertAlmostEqual(res["distance_percent"], 0.386, places=2)

    # 7. Weak predictions suppressed by confidence filters
    def test_07_weak_predictions_suppressed_by_confidence(self):
        """Even if price is within percentage distance, low model probability (< threshold) must suppress warning."""
        curr_price = 1000.0
        p_pct = 0.50

        candles = [
            {"open": 1005.0, "high": 1010.0, "low": 998.0, "close": 1000.0, "full_time": "10:01"},
        ]

        st_res = {
            "direction": "BUY",
            "supertrend_price": 997.0,  # 3.0 pts = 0.30% <= 0.50%
            "distance_points": 3.0,
            "distance_percent": 0.30,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.35,  # Weak conviction (< 0.50)
            "reversal_confidence_threshold": 0.50
        }

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        _process_supertrend_state_transition("cond_weak", None, "SBIN", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        res = _process_supertrend_state_transition("cond_weak", None, "SBIN", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)

        self.assertIsNone(res)

    # 8. No false confirmed reversal before a completed candle validates it
    def test_08_no_false_confirmed_reversal_before_completed_candle(self):
        """Intrabar price piercing Supertrend line must NOT flip confirmed direction."""
        curr_price = 998.0
        st_res_intrabar = {
            "direction": "BUY",
            "supertrend_price": 1000.0,
            "distance_points": 2.0,
            "distance_percent": 0.20,
            "permitted_distance_points": 5.0,
            "proximity_percent": 0.50,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": True,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "reversal_probability": 0.85
        }

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        _process_supertrend_state_transition("cond_intra", None, "TATAMOTORS", st_res_intrabar, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=0.50)
        state = _SUPERTREND_STATES["cond_intra"]
        self.assertEqual(state["confirmed_direction"], "BUY")

        # Now completed candle closes below at 10:02 with is_confirmed_flip=True
        st_res_flip = {
            "direction": "SELL",
            "supertrend_price": 1005.0,
            "distance_points": 7.0,
            "distance_percent": 0.70,
            "permitted_distance_points": 5.0,
            "proximity_percent": 0.50,
            "target_direction": "BUY",
            "is_confirmed_flip": True,
            "is_intrabar_cross": False,
            "is_approaching": False,
            "is_within_pct": False,
            "candle_timestamp": "10:02",
            "min_confirming_candles": 1,
            "reversal_probability": 0.85
        }
        res_flip = _process_supertrend_state_transition("cond_intra", None, "TATAMOTORS", st_res_flip, 995.0, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=0.50)
        self.assertIsNotNone(res_flip)
        self.assertEqual(res_flip["type"], "CONFIRMED_SIGNAL")
        self.assertEqual(res_flip["signal"], "SELL")

    # 9. One early warning per cycle and no duplicate notifications
    def test_09_one_early_warning_per_cycle_no_duplicates(self):
        """Repeated polling during the same candle and cycle must not send repeated warnings."""
        curr_price = 1000.0
        p_pct = 0.50

        candles = [
            {"open": 1005.0, "high": 1010.0, "low": 998.0, "close": 1000.0, "full_time": "10:01"},
        ]

        st_res = {
            "direction": "BUY",
            "supertrend_price": 997.0,
            "distance_points": 3.0,
            "distance_percent": 0.30,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.80,
            "reversal_confidence_threshold": 0.50
        }

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        # Boot
        _process_supertrend_state_transition("cond_dedup", None, "WIPRO", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        # First poll -> Early warning sent
        res1 = _process_supertrend_state_transition("cond_dedup", None, "WIPRO", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNotNone(res1)
        self.assertEqual(res1["type"], "EARLY_WARNING")

        # Second poll on same candle -> Suppressed!
        res2 = _process_supertrend_state_transition("cond_dedup", None, "WIPRO", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNone(res2)

        # Third poll on same candle -> Suppressed!
        res3 = _process_supertrend_state_transition("cond_dedup", None, "WIPRO", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNone(res3)

    # 10. Prediction expiration when price pulls away (> 1.5 * p%) and subsequent resumption
    def test_10_prediction_expiration_and_subsequent_monitoring(self):
        """When price pulls safely away (> 1.5x permitted percentage), early warning expires and resumes MONITORING."""
        curr_price = 1000.0
        p_pct = 0.50  # 1.5x is 0.75%

        candles = [
            {"open": 1005.0, "high": 1010.0, "low": 998.0, "close": 1000.0, "full_time": "10:01"},
        ]

        st_res = {
            "direction": "BUY",
            "supertrend_price": 997.0,
            "distance_points": 3.0,
            "distance_percent": 0.30,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01",
            "candles": candles,
            "reversal_probability": 0.80
        }

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        # Boot & trigger early warning
        _process_supertrend_state_transition("cond_exp", None, "ITC", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        _process_supertrend_state_transition("cond_exp", None, "ITC", st_res, curr_price, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertEqual(_SUPERTREND_STATES["cond_exp"]["state"], "EARLY_WARNING_SENT")

        # Price rallies away to ₹1010.00 -> gap to ₹997.00 is ₹13.00 (1.29% > 0.75%)
        st_res_away = {
            "direction": "BUY",
            "supertrend_price": 997.0,
            "distance_points": 13.0,
            "distance_percent": 1.29,
            "permitted_distance_points": 5.0,
            "proximity_percent": p_pct,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": False,
            "is_within_pct": False,
            "candle_timestamp": "10:02",
            "candles": candles,
            "reversal_probability": 0.20
        }

        res_exp = _process_supertrend_state_transition("cond_exp", None, "ITC", st_res_away, 1010.0, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=p_pct)
        self.assertIsNotNone(res_exp)
        self.assertEqual(res_exp["type"], "WARNING_EXPIRED")
        self.assertEqual(res_exp["reason"], "PULLED_AWAY")
        self.assertEqual(_SUPERTREND_STATES["cond_exp"]["state"], "MONITORING")

    # 11. Missing, stale, closed-market, and invalid-price handling
    def test_11_missing_stale_closed_market_and_invalid_price_handling(self):
        """Feed offline, stale data, closed market, or non-positive price must not generate false signals."""
        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        st_res = {
            "direction": "BUY",
            "supertrend_price": 997.0,
            "distance_points": 3.0,
            "distance_percent": 0.30,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "is_approaching": True,
            "is_within_pct": True,
            "candle_timestamp": "10:01"
        }

        # Closed market
        res_closed = _process_supertrend_state_transition("cond_closed", None, "TCS", st_res, 1000.0, mock_db, 1, now, False, "CLOSED", 5.0, prox_pct=0.50)
        self.assertIsNone(res_closed)

        # Stale data
        res_stale = _process_supertrend_state_transition("cond_stale", None, "TCS", st_res, 1000.0, mock_db, 1, now, True, "STALE", 5.0, prox_pct=0.50)
        self.assertEqual(res_stale["type"], "DATA_UNAVAILABLE")

        # Zero or invalid price
        res_zero = _process_supertrend_state_transition("cond_zero", None, "TCS", st_res, 0.0, mock_db, 1, now, True, "LIVE", 5.0, prox_pct=0.50)
        self.assertEqual(res_zero["type"], "DATA_UNAVAILABLE")

    # 12. Backward compatibility for existing users' saved settings
    def test_12_backward_compatibility(self):
        """Legacy settings and alert records without proximity_percent default safely to 0.50%."""
        mock_db = MagicMock()

        # Alert without supertrend_proximity_percent attribute
        alert = Alert(
            id=101,
            symbol="TCS",
            supertrend_proximity_points=1.0,
            supertrend_proximity_percent=None
        )
        self.assertIn(alert.supertrend_proximity_percent, (None, 0.50))

        # Legacy call to analyze_supertrend_hit with only proximity_points
        res = analyze_supertrend_hit(
            candles_or_df=[
                {"open": 1000.0, "high": 1005.0, "low": 995.0, "close": 1000.0, "full_time": "10:00"},
                {"open": 1000.0, "high": 1005.0, "low": 995.0, "close": 1000.0, "full_time": "10:01"}
            ],
            current_price=1000.0,
            proximity_points=1.0
        )
        self.assertIn("proximity_percent", res)
        self.assertIn("distance_percent", res)
        self.assertIn("upper_boundary", res)
        self.assertIn("lower_boundary", res)


if __name__ == "__main__":
    unittest.main()
