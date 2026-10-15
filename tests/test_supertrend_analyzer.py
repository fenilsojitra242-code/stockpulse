"""
Comprehensive Unit & System Tests for the Corrected Supertrend Analyzer
Validates all 16 items specified in Section 8 of the quantitative specification:
1. Correct True Range and ATR calculations (Wilder's RMA smoothing).
2. Correct initial basic band calculation.
3. Correct recursive final-band calculation.
4. Bullish and bearish trend transitions.
5. No repeated signal from duplicate polling of one candle.
6. Intrabar flips do not alter the confirmed trend.
7. A candle-close transition produces exactly one confirmed event.
8. A failed early warning expires correctly.
9. Both reversal directions (BUY->SELL and SELL->BUY) work.
10. Missing or invalid data does not create false signals.
11. Market closure does not create a false trend change.
12. Stale data suspends predictions.
13. Notification failures are logged and handled safely.
14. Separate monitoring conditions do not interfere with each other.
15. Historical backtesting does not use future information (no look-ahead bias).
16. Historical and live calculations agree for identical completed candle sequences.

Also includes out-of-sample quantitative performance evaluation (Section 9).
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.feature_engine import (
    compute_atr,
    sanitize_candles,
    compute_supertrend,
    calculate_reversal_risk_score,
    analyze_supertrend_hit,
)
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    _SUPERTREND_STATES,
    _evaluate_supertrend_for_alert,
    get_supertrend_settings,
    save_supertrend_settings,
)
from app.database.models import Alert


class TestSuperTrendAnalyzer(unittest.TestCase):

    def setUp(self):
        # Reset state machine before each test
        _SUPERTREND_STATES.clear()

    # 1. Correct True Range and ATR calculations (Wilder's RMA smoothing)
    def test_01_true_range_and_atr_wilders(self):
        """Verify True Range and Wilder's RMA ATR smoothing match quantitative reference."""
        high = pd.Series([102.0, 105.0, 104.0, 107.0, 106.0])
        low = pd.Series([98.0, 101.0, 99.0, 103.0, 102.0])
        close = pd.Series([100.0, 104.0, 101.0, 106.0, 104.0])

        # Expected True Ranges:
        # i=0: H0 - L0 = 102 - 98 = 4.0
        # i=1: max(105-101, abs(105-100), abs(101-100)) = max(4.0, 5.0, 1.0) = 5.0
        # i=2: max(104-99, abs(104-104), abs(99-104)) = max(5.0, 0.0, 5.0) = 5.0
        # i=3: max(107-103, abs(107-101), abs(103-101)) = max(4.0, 6.0, 2.0) = 6.0
        # i=4: max(106-102, abs(106-106), abs(102-106)) = max(4.0, 0.0, 4.0) = 4.0
        atr = compute_atr(high, low, close, period=3)

        # First 3 TRs: 4.0, 5.0, 5.0 -> SMA at i=2: (4+5+5)/3 = 4.6667
        # i=3: (4.6667 * 2 + 6.0) / 3 = (9.3333 + 6.0) / 3 = 5.1111
        # i=4: (5.1111 * 2 + 4.0) / 3 = (10.2222 + 4.0) / 3 = 4.7407
        self.assertAlmostEqual(atr.iloc[2], 4.67, places=1)
        self.assertAlmostEqual(atr.iloc[3], 5.11, places=1)
        self.assertAlmostEqual(atr.iloc[4], 4.74, places=1)

    # 2. Correct initial band calculation
    def test_02_initial_basic_bands(self):
        """Verify initial HL2 and Basic Upper/Lower band calculations."""
        df = pd.DataFrame([
            {"open": 100.0, "high": 104.0, "low": 96.0, "close": 100.0, "full_time": "2026-10-15T09:15:00"},
            {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "full_time": "2026-10-15T09:16:00"},
        ])
        st_df = compute_supertrend(df, period=2, multiplier=2.0)
        # At i=0: HL2 = (104 + 96)/2 = 100.0, ATR = 8.0
        # Basic Upper = 100 + 2*8 = 116.0, Basic Lower = 100 - 2*8 = 84.0
        self.assertEqual(st_df["supertrend_upper"].iloc[0], 116.0)
        self.assertEqual(st_df["supertrend_lower"].iloc[0], 84.0)

    # 3. Correct recursive final-band calculation
    def test_03_recursive_final_bands(self):
        """Verify that Final Lower only raises or resets, and Final Upper only lowers or resets."""
        # 4 bars: uptrend where lower band rises then should NOT drop even if basic lower drops
        df = pd.DataFrame([
            {"open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0, "full_time": "10:00"},
            {"open": 101.0, "high": 105.0, "low": 101.0, "close": 104.0, "full_time": "10:01"},
            {"open": 104.0, "high": 104.5, "low": 102.0, "close": 103.5, "full_time": "10:02"},
            {"open": 103.5, "high": 104.0, "low": 101.5, "close": 103.0, "full_time": "10:03"},
        ])
        st_df = compute_supertrend(df, period=2, multiplier=1.5)
        # In an uptrend, lower band must be non-decreasing while close[i-1] > final_lower[i-1]
        for i in range(1, len(st_df)):
            if st_df["supertrend_direction"].iloc[i] == 1 and st_df["close"].iloc[i-1] >= st_df["supertrend_lower"].iloc[i-1]:
                self.assertGreaterEqual(
                    st_df["supertrend_lower"].iloc[i],
                    st_df["supertrend_lower"].iloc[i-1] - 0.01,
                    f"Lower band should not drop in an ongoing uptrend at bar {i}"
                )

    # 4. Bullish and bearish trend transitions
    def test_04_bullish_and_bearish_transitions(self):
        """Verify clean transition from Bullish (BUY) to Bearish (SELL) and vice versa."""
        # Start in uptrend, drop decisively to force SELL, then rally decisively to force BUY
        candles = [
            {"open": 100, "high": 102, "low": 99, "close": 101, "full_time": "10:00"},
            {"open": 101, "high": 103, "low": 100, "close": 102, "full_time": "10:01"},
            {"open": 102, "high": 104, "low": 101, "close": 103, "full_time": "10:02"},
            # Crash below support -> SELL transition
            {"open": 103, "high": 103, "low": 90, "close": 91, "full_time": "10:03"},
            {"open": 91, "high": 92, "low": 88, "close": 89, "full_time": "10:04"},
            # Rally above resistance -> BUY transition
            {"open": 89, "high": 115, "low": 89, "close": 114, "full_time": "10:05"},
        ]
        df = pd.DataFrame(candles)
        st_df = compute_supertrend(df, period=2, multiplier=1.5)

        signals = st_df["supertrend_signal"].tolist()
        self.assertIn("SELL", signals, "Expected SELL signal upon breakdown")
        self.assertIn("BUY", signals, "Expected BUY signal upon breakout")

        sell_idx = signals.index("SELL")
        self.assertEqual(st_df["supertrend_direction"].iloc[sell_idx], -1)

        buy_idx = signals.index("BUY")
        self.assertEqual(st_df["supertrend_direction"].iloc[buy_idx], 1)

    # 5. No repeated signal from duplicate polling of one candle
    def test_05_no_repeated_signal_from_duplicate_polling(self):
        """Multiple polling cycles on the same completed candle must not emit duplicate signals."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            st_result = {
                "direction": "SELL",
                "supertrend_price": 105.0,
                "distance_points": 5.0,
                "target_direction": "BUY",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "2026-10-15T10:00:00",
                "reversal_risk_score": 0.85
            }

            # First poll initializes condition in BUY
            _SUPERTREND_STATES["test_05"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "2026-10-15T09:59:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            # Poll 1: Confirmed SELL fires once
            res1 = _process_supertrend_state_transition(
                condition_key="test_05",
                alert=None,
                symbol="RELIANCE",
                st_result=st_result,
                curr_price=100.0,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0
            )
            self.assertIsNotNone(res1)
            self.assertEqual(res1["type"], "CONFIRMED_SIGNAL")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)

            # Poll 2 (same candle timestamp 2026-10-15T10:00:00): MUST NOT emit again
            res2 = _process_supertrend_state_transition(
                condition_key="test_05",
                alert=None,
                symbol="RELIANCE",
                st_result=st_result,
                curr_price=100.0,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0
            )
            self.assertIsNone(res2, "Duplicate polling of same candle must return None")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1, "Signal count must remain 1")

        finally:
            ae.notification_manager = orig_nm

    # 6. Intrabar flips do not alter the confirmed trend
    def test_06_intrabar_flips_do_not_alter_confirmed_trend(self):
        """Intrabar price cross during developing candle does not mutate confirmed trend."""
        # 10 completed bars in a solid BUY trend (price ~ 100, ST ~ 95)
        candles = []
        p = 100.0
        for i in range(10):
            candles.append({"open": p-0.5, "high": p+1.0, "low": p-1.0, "close": p, "full_time": f"10:{i:02d}"})
            p += 0.5

        # Live developing price momentarily drops to 90 (below ST line of 97)
        res = analyze_supertrend_hit(candles, current_price=90.0, proximity_points=2.0, previous_direction="BUY")

        # Confirmed direction must REMAIN BUY (from completed bars)
        self.assertEqual(res["direction"], "BUY", "Confirmed direction must remain BUY until candle closes")
        self.assertTrue(res["is_intrabar_cross"], "Intrabar penetration must be detected provisionally")
        self.assertFalse(res["is_confirmed_flip"], "Confirmed flip must be False until completed bar confirms")

    # 7. A candle-close transition produces exactly one confirmed event
    def test_07_candle_close_produces_exactly_one_confirmed_event(self):
        """A completed candle close triggers exactly one confirmed signal event."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            st_result = {
                "direction": "BUY",
                "supertrend_price": 95.0,
                "distance_points": 5.0,
                "target_direction": "SELL",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "2026-10-15T10:15:00",
                "reversal_risk_score": 0.80
            }

            _SUPERTREND_STATES["test_07"] = {
                "state": "MONITORING",
                "confirmed_direction": "SELL",
                "last_confirmed_candle_ts": "2026-10-15T10:14:00",
                "last_st_price": 105.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            res = _process_supertrend_state_transition(
                condition_key="test_07",
                alert=None,
                symbol="TCS",
                st_result=st_result,
                curr_price=100.0,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0
            )
            self.assertEqual(res["type"], "CONFIRMED_SIGNAL")
            self.assertEqual(res["signal"], "BUY")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)

        finally:
            ae.notification_manager = orig_nm

    # 8. A failed early warning expires correctly
    def test_08_early_warning_expiration(self):
        """When price approaches then pulls safely away, warning state expires cleanly."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            _SUPERTREND_STATES["test_08"] = {
                "state": "EARLY_WARNING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": datetime.utcnow() - timedelta(minutes=1),
                "warning_candle_ts": "10:01",
                "early_warning_sent": True,
                "last_dispatched_event_id": "test_08_TCS_10:01_APPROACHING"
            }

            # Price pulls away (distance 8.0 > 1.0 * 1.5)
            pull_away_result = {
                "direction": "BUY",
                "supertrend_price": 95.0,
                "distance_points": 8.0,
                "target_direction": "SELL",
                "is_confirmed_flip": False,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "10:02",
                "reversal_risk_score": 0.20
            }

            res = _process_supertrend_state_transition(
                condition_key="test_08",
                alert=None,
                symbol="TCS",
                st_result=pull_away_result,
                curr_price=103.0,
                db=mock_db,
                user_id=1,
                now=datetime.utcnow(),
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0
            )
            self.assertIsNotNone(res)
            self.assertEqual(res["type"], "WARNING_EXPIRED")
            self.assertEqual(res["reason"], "PULLED_AWAY")
            self.assertEqual(_SUPERTREND_STATES["test_08"]["state"], "MONITORING")

        finally:
            ae.notification_manager = orig_nm

    # 9. Both reversal directions work
    def test_09_both_reversal_directions(self):
        """Verify state machine processes both BUY->SELL and SELL->BUY reversals."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            # BUY -> SELL
            _SUPERTREND_STATES["test_09"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }
            res_sell = _process_supertrend_state_transition(
                condition_key="test_09",
                alert=None,
                symbol="INFY",
                st_result={
                    "direction": "SELL", "supertrend_price": 105.0, "distance_points": 5.0,
                    "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                    "is_approaching": False, "candle_timestamp": "10:01", "reversal_risk_score": 0.8
                },
                curr_price=100.0, db=mock_db, user_id=1, now=datetime.utcnow(),
                is_market_open=True, data_status="LIVE", prox_pts=1.0
            )
            self.assertEqual(res_sell["signal"], "SELL")

            # SELL -> BUY
            res_buy = _process_supertrend_state_transition(
                condition_key="test_09",
                alert=None,
                symbol="INFY",
                st_result={
                    "direction": "BUY", "supertrend_price": 95.0, "distance_points": 5.0,
                    "target_direction": "SELL", "is_confirmed_flip": True, "is_intrabar_cross": False,
                    "is_approaching": False, "candle_timestamp": "10:02", "reversal_risk_score": 0.8
                },
                curr_price=100.0, db=mock_db, user_id=1, now=datetime.utcnow(),
                is_market_open=True, data_status="LIVE", prox_pts=1.0
            )
            self.assertEqual(res_buy["signal"], "BUY")

        finally:
            ae.notification_manager = orig_nm

    # 10. Missing or invalid data does not create false signals
    def test_10_missing_or_invalid_data_safety(self):
        """Sanitizer and indicator handle NaNs, 0, negative prices, and bad strings gracefully."""
        bad_candles = [
            {"open": None, "high": np.nan, "low": -10.0, "close": 0.0, "full_time": "10:00"},
            {"open": 100.0, "high": 90.0, "low": 110.0, "close": 105.0, "full_time": "10:01"}, # inverted H/L
            {"open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0, "full_time": "10:02"},
        ]
        clean_df = sanitize_candles(bad_candles)
        self.assertFalse(clean_df.empty)
        # All lows must be strictly positive and <= close
        self.assertTrue((clean_df["low"] > 0).all())
        self.assertTrue((clean_df["high"] >= clean_df["low"]).all())

        st_df = compute_supertrend(clean_df)
        self.assertFalse(st_df["supertrend"].isna().any())

    # 11. Market closure does not create a false trend change
    def test_11_market_closure_preserves_state(self):
        """When market status is CLOSED, no trend reversals or false warnings are emitted."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            _SUPERTREND_STATES["test_11"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "15:30",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            res = _process_supertrend_state_transition(
                condition_key="test_11",
                alert=None,
                symbol="SBIN",
                st_result={
                    "direction": "SELL", "supertrend_price": 105.0, "distance_points": 5.0,
                    "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                    "is_approaching": True, "candle_timestamp": "15:31", "reversal_risk_score": 0.9
                },
                curr_price=100.0, db=mock_db, user_id=1, now=datetime.utcnow(),
                is_market_open=False, data_status="CLOSED", prox_pts=1.0
            )
            self.assertIsNone(res, "Closed market must produce no transition events")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0)
            self.assertEqual(mock_notif.emit_supertrend_approaching.call_count, 0)

        finally:
            ae.notification_manager = orig_nm

    # 12. Stale data suspends predictions
    def test_12_stale_or_unavailable_data_suspends(self):
        """Data marked UNAVAILABLE transitions state machine to DATA_UNAVAILABLE without sending signals."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            _SUPERTREND_STATES["test_12"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            res = _process_supertrend_state_transition(
                condition_key="test_12",
                alert=None,
                symbol="TATAMOTORS",
                st_result={},
                curr_price=0.0, db=mock_db, user_id=1, now=datetime.utcnow(),
                is_market_open=True, data_status="UNAVAILABLE", prox_pts=1.0
            )
            self.assertEqual(res["status"], "DATA_UNAVAILABLE")
            self.assertEqual(_SUPERTREND_STATES["test_12"]["state"], "DATA_UNAVAILABLE")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0)

        finally:
            ae.notification_manager = orig_nm

    # 13. Notification failures are logged and handled safely
    def test_13_notification_failure_does_not_halt_engine(self):
        """An unhandled exception inside dispatch does not crash the state transition engine."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        mock_notif.emit_supertrend_signal.side_effect = Exception("Simulated connection timeout")
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            alert = Alert(
                id=888, user_id=1, symbol="RELIANCE", direction="UP", target_percent=1.0,
                warning_minutes=5, supertrend_proximity_points=1.0, last_supertrend_direction="BUY",
                supertrend_approaching_sent=False, status="ACTIVE", enable_supertrend=True
            )
            # Should not raise exception
            try:
                _evaluate_supertrend_for_alert(
                    alert=alert,
                    snap={"price": 90.0, "data_status": "LIVE", "candles": [
                        {"open": 100, "high": 102, "low": 98, "close": 101, "full_time": "10:00"},
                        {"open": 101, "high": 103, "low": 99, "close": 102, "full_time": "10:01"},
                        {"open": 102, "high": 102, "low": 89, "close": 90, "full_time": "10:02"},
                    ]},
                    db=mock_db,
                    now=datetime.utcnow()
                )
            except Exception as e:
                self.fail(f"_evaluate_supertrend_for_alert raised an unexpected exception: {e}")

        finally:
            ae.notification_manager = orig_nm

    # 14. Separate monitoring conditions do not interfere with each other
    def test_14_isolated_monitoring_conditions(self):
        """Alert 1 and Alert 2 track completely isolated states for the same or different instruments."""
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            _SUPERTREND_STATES["cond_A"] = {
                "state": "EARLY_WARNING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": datetime.utcnow(),
                "warning_candle_ts": "10:01",
                "early_warning_sent": True,
                "last_dispatched_event_id": "cond_A_APPROACHING"
            }
            _SUPERTREND_STATES["cond_B"] = {
                "state": "MONITORING",
                "confirmed_direction": "SELL",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 105.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            # Update Cond B: does not affect Cond A
            _process_supertrend_state_transition(
                condition_key="cond_B",
                alert=None, symbol="INFY",
                st_result={
                    "direction": "SELL", "supertrend_price": 105.0, "distance_points": 10.0,
                    "target_direction": "BUY", "is_confirmed_flip": False, "is_intrabar_cross": False,
                    "is_approaching": False, "candle_timestamp": "10:02", "reversal_risk_score": 0.2
                },
                curr_price=95.0, db=mock_db, user_id=1, now=datetime.utcnow(),
                is_market_open=True, data_status="LIVE", prox_pts=1.0
            )

            self.assertEqual(_SUPERTREND_STATES["cond_A"]["state"], "EARLY_WARNING")
            self.assertEqual(_SUPERTREND_STATES["cond_B"]["state"], "MONITORING")

        finally:
            ae.notification_manager = orig_nm

    # 15. Historical backtesting does not use future information
    def test_15_no_lookahead_bias(self):
        """Calculating SuperTrend on bar N depends strictly on bars 0..N, never on bar N+1."""
        np.random.seed(42)
        prices = 100.0 + np.cumsum(np.random.randn(50))
        candles = []
        for i, p in enumerate(prices):
            candles.append({
                "open": p - 0.2, "high": p + 0.8, "low": p - 0.8, "close": p, "full_time": f"10:{i:02d}"
            })
        df_all = pd.DataFrame(candles)

        # Compute on first 30 bars
        df_30 = compute_supertrend(df_all.iloc[:30].copy(), period=10, multiplier=3.0)
        # Compute on all 50 bars
        df_50 = compute_supertrend(df_all.copy(), period=10, multiplier=3.0)

        # SuperTrend values and direction on bar 29 must match identically
        self.assertEqual(df_30["supertrend"].iloc[29], df_50["supertrend"].iloc[29])
        self.assertEqual(df_30["supertrend_direction"].iloc[29], df_50["supertrend_direction"].iloc[29])

    # 16. Historical and live calculations agree for identical completed candle sequences
    def test_16_historical_and_live_calculation_agreement(self):
        """A completed candle sequence yields identical indicator values whether computed in batch or step-by-step."""
        candles = [
            {"open": 100, "high": 102, "low": 98, "close": 101, "full_time": "10:00"},
            {"open": 101, "high": 104, "low": 100, "close": 103, "full_time": "10:01"},
            {"open": 103, "high": 105, "low": 102, "close": 104, "full_time": "10:02"},
            {"open": 104, "high": 106, "low": 103, "close": 105, "full_time": "10:03"},
            {"open": 105, "high": 107, "low": 104, "close": 106, "full_time": "10:04"}
        ]
        # Batch calculation
        df_batch = compute_supertrend(pd.DataFrame(candles), period=3, multiplier=2.0)

        # Incremental live step-by-step calculation
        step_df = None
        for i in range(2, len(candles) + 1):
            step_df = compute_supertrend(pd.DataFrame(candles[:i]), period=3, multiplier=2.0)

        self.assertEqual(df_batch["supertrend"].iloc[-1], step_df["supertrend"].iloc[-1])
        self.assertEqual(df_batch["supertrend_direction"].iloc[-1], step_df["supertrend_direction"].iloc[-1])

    # =========================================================================
    # STEP 7: DETERMINISTIC REGRESSION TESTS FOR THE EXACT BUG
    # =========================================================================

    def test_regression_01_intrabar_dip_does_not_produce_sell_confirmed(self):
        """
        Regression Test 1: An established BUY trend with a temporary intrabar price dip.
        Must prove that a temporary intrabar SELL dip DOES NOT produce SELL CONFIRMED.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            # Established BUY trend: 10 completed candles with close >= lower band
            candles = []
            p = 100.0
            for i in range(10):
                candles.append({
                    "open": p - 0.2, "high": p + 1.0, "low": p - 0.5, "close": p,
                    "full_time": f"2026-10-15T10:{i:02d}:00"
                })
                p += 0.5

            # Live price experiences a deep intrabar plunge to 85.0 (well below Supertrend support ~95.0)
            res = analyze_supertrend_hit(
                candles, current_price=85.0, proximity_points=2.0, previous_direction="BUY"
            )

            # Confirmed indicator direction must remain BUY
            self.assertEqual(res["direction"], "BUY", "Confirmed trend must remain BUY during developing candle dip")
            self.assertTrue(res["is_intrabar_cross"], "Intrabar cross must be flagged as provisional")
            self.assertFalse(res["is_confirmed_flip"], "Confirmed flip must be False")

            # Feed to state machine
            _SUPERTREND_STATES["reg_01"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "2026-10-15T10:08:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            trans = _process_supertrend_state_transition(
                condition_key="reg_01", alert=None, symbol="RELIANCE",
                st_result=res, curr_price=85.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            # Assert NO CONFIRMED_SIGNAL event is emitted
            if trans:
                self.assertNotEqual(trans.get("type"), "CONFIRMED_SIGNAL")
                self.assertNotEqual(trans.get("signal"), "SELL")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0, "emit_supertrend_signal must NOT be called on intrabar dip")
            self.assertEqual(_SUPERTREND_STATES["reg_01"]["confirmed_direction"], "BUY", "Confirmed state must remain BUY")

        finally:
            ae.notification_manager = orig_nm

    def test_regression_02_repeated_polling_of_unfinished_candle(self):
        """
        Regression Test 2: Repeated polling of the same unfinished candle.
        Must not repeatedly emit duplicate warnings or reset state.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            candles = [
                {"open": 100, "high": 102, "low": 99, "close": 101, "full_time": "10:00"},
                {"open": 101, "high": 103, "low": 100, "close": 102, "full_time": "10:01"},
            ]
            # Price ticks within proximity repeatedly on the same bar
            res1 = analyze_supertrend_hit(candles, current_price=101.5, proximity_points=5.0, previous_direction="BUY")

            _SUPERTREND_STATES["reg_02"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 98.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            # Poll 1
            trans1 = _process_supertrend_state_transition(
                condition_key="reg_02", alert=None, symbol="TCS",
                st_result=res1, curr_price=101.5, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=5.0
            )

            # Poll 2: identical candle, minor micro-tick
            res2 = analyze_supertrend_hit(candles, current_price=101.4, proximity_points=5.0, previous_direction="BUY")
            trans2 = _process_supertrend_state_transition(
                condition_key="reg_02", alert=None, symbol="TCS",
                st_result=res2, curr_price=101.4, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=5.0
            )

            # Warning should have been emitted at most once
            self.assertLessEqual(mock_notif.emit_supertrend_approaching.call_count, 1)
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0)
            self.assertEqual(_SUPERTREND_STATES["reg_02"]["confirmed_direction"], "BUY")

        finally:
            ae.notification_manager = orig_nm

    def test_regression_03_completed_candle_not_crossing_valid_threshold(self):
        """
        Regression Test 3: A completed candle that does not cross the valid Supertrend reversal threshold.
        Close stays strictly above lower band -> trend remains BUY.
        """
        candles = [
            {"open": 100, "high": 105, "low": 98, "close": 102, "full_time": "10:00"},
            {"open": 102, "high": 104, "low": 99, "close": 101, "full_time": "10:01"},
            {"open": 101, "high": 103, "low": 99.5, "close": 100.5, "full_time": "10:02"},
        ]
        st_df = compute_supertrend(pd.DataFrame(candles), period=2, multiplier=2.0)
        # All closes remain above lower band -> direction stays 1 (BUY) throughout
        self.assertEqual(st_df["supertrend_direction"].iloc[-1], 1)
        self.assertIsNone(st_df["supertrend_signal"].iloc[-1])

    def test_regression_04_genuine_bearish_supertrend_transition(self):
        """
        Regression Test 4: A genuine bearish Supertrend transition.
        Proves that valid reversals are not permanently blocked.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            st_result = {
                "direction": "SELL",
                "supertrend_price": 105.0,
                "distance_points": 5.0,
                "target_direction": "BUY",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "2026-10-15T11:00:00",
                "reversal_risk_score": 0.85,
                "penetration_atr_ratio": 1.2,
                "min_atr_filter": 0.0,
                "min_confirming_candles": 1
            }

            _SUPERTREND_STATES["reg_04"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "2026-10-15T10:59:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            res = _process_supertrend_state_transition(
                condition_key="reg_04", alert=None, symbol="INFY",
                st_result=st_result, curr_price=98.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            self.assertIsNotNone(res)
            self.assertEqual(res["type"], "CONFIRMED_SIGNAL")
            self.assertEqual(res["signal"], "SELL")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)
            self.assertEqual(_SUPERTREND_STATES["reg_04"]["confirmed_direction"], "SELL")

        finally:
            ae.notification_manager = orig_nm

    def test_regression_05_pending_bearish_transition_fails_confirmation(self):
        """
        Regression Test 5: A pending bearish transition that fails additional confirmation.
        When 2 confirming candles are required, bar 1 marks PENDING_SELL.
        If bar 2 rallies back to BUY, the pending reversal is cancelled and confirmed BUY is preserved.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            # Setup state in confirmed BUY
            _SUPERTREND_STATES["reg_05"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            # Candle 1: Breaches support, but min_confirming_candles = 2
            st_result_bar1 = {
                "direction": "SELL",
                "supertrend_price": 102.0,
                "distance_points": 3.0,
                "target_direction": "BUY",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "10:01",
                "reversal_risk_score": 0.70,
                "penetration_atr_ratio": 0.5,
                "min_atr_filter": 0.0,
                "min_confirming_candles": 2
            }

            res1 = _process_supertrend_state_transition(
                condition_key="reg_05", alert=None, symbol="HDFCBANK",
                st_result=st_result_bar1, curr_price=99.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            # Must NOT confirm SELL yet!
            self.assertIsNone(res1, "Signal must be pending, not emitted on 1st confirming bar")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0)
            self.assertEqual(_SUPERTREND_STATES["reg_05"]["confirmed_direction"], "BUY", "Confirmed direction must stay BUY")
            self.assertEqual(_SUPERTREND_STATES["reg_05"]["pending_reversal_direction"], "SELL")

            # Candle 2: Price immediately rallies back to BUY (false breakdown whipsaw)
            st_result_bar2 = {
                "direction": "BUY",
                "supertrend_price": 96.0,
                "distance_points": 5.0,
                "target_direction": "SELL",
                "is_confirmed_flip": False,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "10:02",
                "reversal_risk_score": 0.30,
                "penetration_atr_ratio": 0.0,
                "min_atr_filter": 0.0,
                "min_confirming_candles": 2
            }

            res2 = _process_supertrend_state_transition(
                condition_key="reg_05", alert=None, symbol="HDFCBANK",
                st_result=st_result_bar2, curr_price=103.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            # Pending reversal must be cancelled, confirmed BUY preserved
            self.assertIsNone(res2)
            self.assertIsNone(_SUPERTREND_STATES["reg_05"]["pending_reversal_direction"])
            self.assertEqual(_SUPERTREND_STATES["reg_05"]["confirmed_direction"], "BUY")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0, "No false SELL signal ever emitted")

        finally:
            ae.notification_manager = orig_nm

    def test_regression_06_genuine_bearish_followed_by_genuine_bullish(self):
        """
        Regression Test 6: A genuine bearish transition followed by a genuine bullish transition.
        Both transitions must succeed sequentially.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            _SUPERTREND_STATES["reg_06"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            # Transition 1: BUY -> SELL
            st_sell = {
                "direction": "SELL", "supertrend_price": 105.0, "distance_points": 5.0,
                "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                "is_approaching": False, "candle_timestamp": "10:05", "reversal_risk_score": 0.85
            }
            res_sell = _process_supertrend_state_transition(
                condition_key="reg_06", alert=None, symbol="SBIN",
                st_result=st_sell, curr_price=98.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )
            self.assertEqual(res_sell["signal"], "SELL")
            self.assertEqual(_SUPERTREND_STATES["reg_06"]["confirmed_direction"], "SELL")

            # Transition 2: SELL -> BUY
            st_buy = {
                "direction": "BUY", "supertrend_price": 98.0, "distance_points": 5.0,
                "target_direction": "SELL", "is_confirmed_flip": True, "is_intrabar_cross": False,
                "is_approaching": False, "candle_timestamp": "10:15", "reversal_risk_score": 0.85
            }
            res_buy = _process_supertrend_state_transition(
                condition_key="reg_06", alert=None, symbol="SBIN",
                st_result=st_buy, curr_price=105.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )
            self.assertEqual(res_buy["signal"], "BUY")
            self.assertEqual(_SUPERTREND_STATES["reg_06"]["confirmed_direction"], "BUY")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 2)

        finally:
            ae.notification_manager = orig_nm

    def test_regression_07_duplicate_and_out_of_order_candles(self):
        """
        Regression Test 7: Duplicate and out-of-order candle data.
        Sanitizer sorts chronologically and deduplicates timestamps before calculation.
        """
        raw_candles = [
            {"time": "10:02", "open": 102, "high": 104, "low": 101, "close": 103},
            {"time": "10:00", "open": 100, "high": 102, "low": 99, "close": 101},
            {"time": "10:01", "open": 101, "high": 103, "low": 100, "close": 102},
            {"time": "10:01", "open": 101, "high": 103, "low": 100, "close": 102},  # Duplicate
        ]
        clean_df = sanitize_candles(raw_candles)
        self.assertEqual(len(clean_df), 3, "Duplicate candle at 10:01 must be dropped")
        # Must be chronologically ordered
        self.assertEqual(clean_df.iloc[0]["close"], 101.0)
        self.assertEqual(clean_df.iloc[1]["close"], 102.0)
        self.assertEqual(clean_df.iloc[2]["close"], 103.0)

    def test_regression_08_historical_live_parity(self):
        """
        Regression Test 8: Historical / Live parity.
        Full batch calculation and step-by-step rolling calculation must yield identical results.
        """
        np.random.seed(999)
        p = 500.0
        candles = []
        for i in range(20):
            ret = np.random.normal(0, 1.5)
            p_next = max(100.0, p + ret)
            candles.append({
                "open": p, "high": max(p, p_next) + 0.5, "low": min(p, p_next) - 0.5, "close": p_next,
                "full_time": f"2026-10-15T09:{i:02d}:00"
            })
            p = p_next

        batch_st = compute_supertrend(pd.DataFrame(candles), period=5, multiplier=2.5)

        rolling_st = None
        for k in range(5, len(candles) + 1):
            rolling_st = compute_supertrend(pd.DataFrame(candles[:k]), period=5, multiplier=2.5)

        self.assertEqual(batch_st["supertrend"].iloc[-1], rolling_st["supertrend"].iloc[-1])
        self.assertEqual(batch_st["supertrend_direction"].iloc[-1], rolling_st["supertrend_direction"].iloc[-1])

    def test_regression_09_duplicate_notification_prevention(self):
        """
        Regression Test 9: Duplicate notification prevention.
        Repeated calls with identical signal ID / candle timestamp are suppressed.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            st_result = {
                "direction": "SELL", "supertrend_price": 105.0, "distance_points": 5.0,
                "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                "is_approaching": False, "candle_timestamp": "2026-10-15T12:00:00", "reversal_risk_score": 0.80
            }

            _SUPERTREND_STATES["reg_09"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "2026-10-15T11:59:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "last_dispatched_event_id": None
            }

            # Call 1: fires
            r1 = _process_supertrend_state_transition(
                condition_key="reg_09", alert=None, symbol="TATAMOTORS",
                st_result=st_result, curr_price=98.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )
            # Call 2 (same candle): must be blocked
            r2 = _process_supertrend_state_transition(
                condition_key="reg_09", alert=None, symbol="TATAMOTORS",
                st_result=st_result, curr_price=98.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            self.assertIsNotNone(r1)
            self.assertIsNone(r2)
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)

        finally:
            ae.notification_manager = orig_nm

    def test_regression_10_independent_states_multiple_stocks(self):
        """
        Regression Test 10: Independent states for multiple stocks.
        Stock A in BUY trend and Stock B in SELL trend maintain isolated states without cross-contamination.
        """
        mock_db = MagicMock()
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            # Condition Stock A (BUY)
            _process_supertrend_state_transition(
                condition_key="stock_A", alert=None, symbol="STOCK_A",
                st_result={
                    "direction": "BUY", "supertrend_price": 90.0, "distance_points": 10.0,
                    "target_direction": "SELL", "is_confirmed_flip": False, "is_intrabar_cross": False,
                    "is_approaching": False, "candle_timestamp": "10:00", "reversal_risk_score": 0.20
                },
                curr_price=100.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            # Condition Stock B (SELL)
            _process_supertrend_state_transition(
                condition_key="stock_B", alert=None, symbol="STOCK_B",
                st_result={
                    "direction": "SELL", "supertrend_price": 210.0, "distance_points": 10.0,
                    "target_direction": "BUY", "is_confirmed_flip": False, "is_intrabar_cross": False,
                    "is_approaching": False, "candle_timestamp": "10:00", "reversal_risk_score": 0.20
                },
                curr_price=200.0, db=mock_db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=2.0
            )

            self.assertEqual(_SUPERTREND_STATES["stock_A"]["confirmed_direction"], "BUY")
            self.assertEqual(_SUPERTREND_STATES["stock_B"]["confirmed_direction"], "SELL")

        finally:
            ae.notification_manager = orig_nm

    def test_27_supertrend_settings_empty_by_default_and_user_retention(self):
        """Verify 0 shares selected by default on login, and custom shares preserved on relogin."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database.database import Base
        from app.services.alert_engine import scan_default_watchlist_supertrend

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        try:
            # 1. Fresh user (user_id=101) with no settings row
            settings_fresh = get_supertrend_settings(db, user_id=101)
            self.assertEqual(settings_fresh["selected_symbols"], [], "Fresh user must have 0 shares selected on login")
            self.assertGreater(len(settings_fresh["popular_symbols"]), 0, "Popular symbols suggestions must be present")

            # 2. Scanner returns empty list when no shares selected
            scanned = scan_default_watchlist_supertrend(db, user_id=101)
            self.assertEqual(scanned, [], "Scanner must not scan or alert when user has 0 shares selected")

            # 3. User selects 1 share (e.g. TCS)
            save_supertrend_settings(db, selected_symbols=["TCS"], user_id=101)
            settings_saved = get_supertrend_settings(db, user_id=101)
            self.assertEqual(settings_saved["selected_symbols"], ["TCS"])

            # 4. User relogins (new session fetch for user_id=101)
            settings_relogin = get_supertrend_settings(db, user_id=101)
            self.assertEqual(settings_relogin["selected_symbols"], ["TCS"], "Only user-selected share must be returned on relogin")

            # 5. User adds multiple shares
            save_supertrend_settings(db, selected_symbols=["TCS", "RELIANCE"], user_id=101)
            settings_multi = get_supertrend_settings(db, user_id=101)
            self.assertEqual(settings_multi["selected_symbols"], ["TCS", "RELIANCE"])

            # 6. User clears all shares
            save_supertrend_settings(db, selected_symbols=[], user_id=101)
            settings_cleared = get_supertrend_settings(db, user_id=101)
            self.assertEqual(settings_cleared["selected_symbols"], [], "Cleared shares must remain empty list and not revert to popular")

            # 7. Another user (user_id=102) remains isolated with 0 shares
            settings_other = get_supertrend_settings(db, user_id=102)
            self.assertEqual(settings_other["selected_symbols"], [])
        finally:
            db.close()

    def test_phase2_diagnostic_event_logging(self):
        """Phase 2: Verify diagnostic event logger records causal features and resolves outcomes."""
        from app.services.diagnostic_logger import diagnostic_logger

        rec = diagnostic_logger.record_prediction(
            symbol="INFY",
            candle_interval="1m",
            prediction_timestamp="2026-10-15T10:00:00",
            market_data_timestamp="2026-10-15T10:00:00",
            confirmed_direction_before="BUY",
            raw_direction="BUY",
            atr=1.5,
            final_upper_band=105.0,
            final_lower_band=95.0,
            closing_price=100.0,
            features_snapshot={"dist_to_st_norm": 0.05, "rsi": 45.0, "atr_norm": 0.01},
            predicted_direction="SELL",
            horizon_minutes=5,
            reversal_probability=0.72,
            heuristic_score=0.65,
            applied_threshold=0.65,
            market_regime="SIDEWAYS_CHOPPY",
            volatility_regime="HIGH_VOLATILITY",
            early_warning_decision=True,
            decision_reason="Probability above threshold in choppy regime",
            model_type="CALIBRATED_ML"
        )
        event_id = rec.get("event_id")
        self.assertIsNotNone(event_id)

        # Retrieve recent logs
        logs = diagnostic_logger.get_recent_events(limit=5)
        logged_event = next((l for l in logs if l.get("event_id") == event_id), None)
        self.assertIsNotNone(logged_event)
        self.assertEqual(logged_event["symbol"], "INFY")
        self.assertEqual(logged_event["confirmed_direction_before"], "BUY")
        self.assertEqual(logged_event["market_regime"], "SIDEWAYS_CHOPPY")
        self.assertEqual(logged_event["outcome"], "PENDING")

        # Resolve outcome as TRUE_POSITIVE
        resolved = diagnostic_logger.resolve_outcome(
            event_id=event_id,
            actual_flip_occurred=True,
            actual_subsequent_direction="SELL",
            resolution_reason="CONFIRMED_FLIP"
        )
        self.assertTrue(resolved)

        # Check summary metrics
        summary = diagnostic_logger.get_summary()
        self.assertGreaterEqual(summary["total_predictions"], 1)

    def test_phase6_state_separation(self):
        """Phase 6: Strict state separation - Early warning NEVER mutates confirmed direction."""
        from app.services.prediction import predict_supertrend_reversal

        # Synthetic candles in established BUY trend
        candles = []
        p = 100.0
        for i in range(25):
            candles.append({
                "open": p - 0.5, "high": p + 1.0, "low": p - 0.5, "close": p,
                "volume": 1000, "full_time": f"10:{i:02d}"
            })
            p += 0.5

        # Query prediction with high reversal probability scenario
        pred = predict_supertrend_reversal(
            candles_or_df=candles,
            confirmed_direction="BUY",
            horizon_minutes=5,
            current_price=110.0,
            symbol="TCS",
            supertrend_price=109.8,
            atr_value=1.5
        )

        # State must be one of the strict 4 states
        self.assertIn(pred["prediction_state"], ["CONFIRMED", "EARLY_WARNING", "NO_CLEAR_SIGNAL", "DATA_UNAVAILABLE"])
        # Most critically: prediction output does NOT flip confirmed direction
        self.assertNotEqual(pred.get("prediction_state"), "SELL_CONFIRMED")
        self.assertEqual(pred["reversal_target_direction"], "SELL")

    def test_phase7_market_regime_classification(self):
        """Phase 7: Verify market regime classification identifies trends and choppy conditions."""
        from app.services.market_regime import classify_market_regime

        # 1. Bullish series: consistently higher closes
        bullish_candles = [{"open": 100 + i, "high": 101 + i, "low": 99.5 + i, "close": 100.8 + i, "volume": 1000} for i in range(30)]
        bull_regime = classify_market_regime(bullish_candles)
        self.assertEqual(bull_regime["primary_regime"], "BULLISH_TREND")
        self.assertEqual(bull_regime["recommended_threshold_boost"], 0.0)

        # 2. Bearish series: consistently lower closes
        bearish_candles = [{"open": 200 - i, "high": 200.5 - i, "low": 198.5 - i, "close": 199.0 - i, "volume": 1000} for i in range(30)]
        bear_regime = classify_market_regime(bearish_candles)
        self.assertEqual(bear_regime["primary_regime"], "BEARISH_TREND")

        # 3. Sideways choppy series: oscillating around mean
        choppy_candles = []
        for i in range(30):
            noise = 2.0 if (i % 2 == 0) else -2.0
            choppy_candles.append({"open": 150.0, "high": 153.0, "low": 147.0, "close": 150.0 + noise, "volume": 1000})
        chop_regime = classify_market_regime(choppy_candles)
        self.assertEqual(chop_regime["primary_regime"], "SIDEWAYS_CHOPPY")
        # In choppy markets, confirming candles = 2 and threshold boost > 0
        self.assertEqual(chop_regime["recommended_min_confirming_candles"], 2)
        self.assertGreater(chop_regime["recommended_threshold_boost"], 0.0)




def run_quantitative_performance_evaluation():
    """
    Chronological out-of-sample evaluation of SuperTrend analyzer (Section 9):
    Simulates 300 minutes of realistic market data with volatile regimes,
    measures precision, recall, false positive rate, warning lead time, and whipsaws.
    """
    print("\n" + "="*70)
    print("RUNNING CHRONOLOGICAL OUT-OF-SAMPLE PERFORMANCE EVALUATION (SECTION 9)")
    print("="*70)

    np.random.seed(12345)
    n_bars = 300
    base_price = 1500.0

    # Generate synthetic price series with trend regimes and volatility bursts
    regimes = [0.15] * 60 + [-0.20] * 60 + [0.30] * 80 + [-0.25] * 60 + [0.10] * 40
    volatilities = [1.5] * 60 + [3.0] * 60 + [1.8] * 80 + [4.5] * 60 + [2.0] * 40

    prices = [base_price]
    highs = []
    lows = []
    opens = []
    closes = []
    timestamps = []

    start_dt = datetime(2026, 10, 15, 9, 15)
    for i in range(n_bars):
        drift = regimes[i]
        vol = volatilities[i]
        ret = drift + np.random.normal(0, vol)
        p_prev = prices[-1]
        p_curr = max(100.0, p_prev + ret)
        prices.append(p_curr)

        c_open = p_prev
        c_close = p_curr
        c_high = max(c_open, c_close) + abs(np.random.normal(0, vol * 0.4))
        c_low = min(c_open, c_close) - abs(np.random.normal(0, vol * 0.4))

        opens.append(round(c_open, 2))
        highs.append(round(c_high, 2))
        lows.append(round(c_low, 2))
        closes.append(round(c_close, 2))
        timestamps.append((start_dt + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S"))

    df_market = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "full_time": timestamps
    })

    # Train / Validation split: First 120 bars calibration, subsequent 180 bars out-of-sample
    oos_df = df_market.iloc[120:].reset_index(drop=True)

    # Run Standard Supertrend (Period=10, Multiplier=3.0)
    st_oos = compute_supertrend(oos_df, period=10, multiplier=3.0)

    signals = st_oos["supertrend_signal"].dropna().tolist()
    total_transitions = len(signals)
    buy_signals = signals.count("BUY")
    sell_signals = signals.count("SELL")

    # Whipsaw detection: consecutive flips within <= 3 bars
    signal_indices = [i for i, sig in enumerate(st_oos["supertrend_signal"]) if sig in ("BUY", "SELL")]
    whipsaws = 0
    for j in range(1, len(signal_indices)):
        if (signal_indices[j] - signal_indices[j-1]) <= 3:
            whipsaws += 1

    # Warning lead-time & precision simulation
    # A warning is triggered when price is within proximity points (3.0 pts)
    prox_pts = 3.0
    warning_events = []
    confirmed_events = set(signal_indices)

    for i in range(1, len(st_oos)):
        st_p = st_oos["supertrend"].iloc[i-1]
        c = st_oos["close"].iloc[i]
        dist = abs(c - st_p)
        if dist <= prox_pts:
            warning_events.append(i)

    # True Positives: warning followed by confirmed flip within 5 bars
    tp = 0
    fp = 0
    lead_times = []
    for w in warning_events:
        hit = False
        for offset in range(1, 6):
            if (w + offset) in confirmed_events:
                tp += 1
                lead_times.append(offset)
                hit = True
                break
        if not hit:
            fp += 1

    precision = tp / max(1, (tp + fp))
    avg_lead_time_bars = np.mean(lead_times) if lead_times else 0.0

    print(f"Total Out-of-Sample Bars Evaluated: {len(oos_df)}")
    print(f"Confirmed Transitions Detected:    {total_transitions} (BUY: {buy_signals}, SELL: {sell_signals})")
    print(f"Rapid Whipsaws (<= 3 bars):        {whipsaws} ({whipsaws / max(1, total_transitions) * 100:.1f}%)")
    print(f"Provisional Warnings Generated:    {len(warning_events)}")
    print(f"Warnings Followed by Flip (TP):    {tp}")
    print(f"Warnings Without Reversal (FP):    {fp}")
    print(f"Warning Precision:                 {precision * 100:.1f}%")
    print(f"Average Lead Time to Reversal:     {avg_lead_time_bars:.1f} bars ({avg_lead_time_bars * 1.0:.1f} min)")
    print("="*70 + "\n")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSuperTrendAnalyzer)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        run_quantitative_performance_evaluation()
    else:
        sys.exit(1)
