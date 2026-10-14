from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from app.services.messaging.events import NotificationEventType

# IST offset from UTC
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_str() -> str:
    """Returns current IST time as HH:MM:SS string."""
    return datetime.now(_IST).strftime("%H:%M IST")


class MessageBuilder:
    """
    Centralized Message Builder for StockPulse.
    Receives structured alert/market data and produces human-readable,
    channel-optimized messages (plain text, HTML for Popups, short summaries for Dashboard).
    Enforces non-guaranteed, probabilistic language for early-warning messages.
    """

    @classmethod
    def build(cls, event_type: str, data: Dict[str, Any]) -> Dict[str, str]:
        ev = str(event_type).upper()
        symbol = data.get("symbol", "N/A").upper()
        target_pct = float(data.get("target_percentage") or data.get("target_percent") or 0.0)
        window = int(data.get("warning_window") or data.get("warning_minutes") or 0)
        direction = str(data.get("direction", "UP")).upper()
        current_price = data.get("current_price")
        start_price = data.get("start_price")
        movement = data.get("current_movement") or data.get("current_change") or 0.0
        prob = data.get("probability")
        threshold = data.get("threshold", "Adaptive")
        time_str = data.get("timestamp") or _now_ist_str()

        # Format price strings safely
        curr_price_str = f"₹{current_price:,.2f}" if current_price is not None else "N/A"
        start_price_str = f"₹{start_price:,.2f}" if start_price is not None else curr_price_str
        dir_sign = "+" if direction == "UP" else ("-" if direction == "DOWN" else "±")
        move_sign = "+" if movement >= 0 else ""

        if prob is not None:
            prob_pct = int(prob * 100) if prob <= 1.0 else int(prob)
            prob_str = f"{prob_pct}%"
        else:
            prob_str = "Pending first evaluation"

        if isinstance(threshold, float):
            thresh_str = f"{int(threshold * 100)}%" if threshold <= 1.0 else f"{int(threshold)}%"
        else:
            thresh_str = "Adaptive"

        target_p_val = data.get("target_price")
        target_p_str = f"₹{target_p_val:,.2f}" if target_p_val is not None else f"{dir_sign}{target_pct:.2f}%"

        if ev == NotificationEventType.MONITORING_STARTED.value:
            title = f"🤖 Monitoring Active: {symbol} ({direction})"
            plain_text = (
                f"StockPulse Prediction & Monitoring Active\n\n"
                f"Stock: {symbol}\n"
                f"Target: {dir_sign}{target_pct:.2f}%\n"
                f"Direction: {direction}\n"
                f"Prediction Horizon: {window} minutes\n"
                f"Live Entry Price: {curr_price_str}\n"
                f"Model Probability: {prob_str}\n\n"
                f"Status: MONITORING & PREDICTION ACTIVE\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"🤖 <b>StockPulse Prediction Active</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Target:</b> <b>{dir_sign}{target_pct:.2f}%</b>\n"
                f"<b>Direction:</b> <b>{direction}</b>\n"
                f"<b>Prediction Horizon:</b> {window} minutes\n"
                f"<b>Live Entry Price:</b> {curr_price_str}\n"
                f"<b>Model Probability:</b> <b>{prob_str}</b>\n\n"
                f"<b>Status:</b> <i>Monitoring &amp; Prediction Active</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"{symbol} monitoring active: Target {dir_sign}{target_pct:.2f}% in {window}m (Prob: {prob_str})"

        elif ev == NotificationEventType.EARLY_WARNING.value:
            title = f"🚨 Early Warning: {symbol} Target Predicted"
            body_desc = (
                f"Your target ({dir_sign}{target_pct:.2f}%) is predicted to be achieved in {window} minutes!"
            )

            plain_text = (
                f"StockPulse Early Warning Prediction\n\n"
                f"{body_desc}\n\n"
                f"Stock: {symbol}\n"
                f"Target: {dir_sign}{target_pct:.2f}%\n"
                f"Direction: {direction}\n"
                f"Prediction Window: {window} minutes\n"
                f"Current Price: {curr_price_str}\n"
                f"Model Probability: {prob_str}\n"
                f"System Adaptive Threshold: {thresh_str}\n\n"
                f"Status: EARLY WARNING (Prediction Active)\n"
                f"Time: {time_str}\n\n"
                f"Verifying target outcome over the next {window} minutes..."
            )

            html_text = (
                f"🚨 <b>StockPulse Early Warning Prediction</b>\n\n"
                f"<b>{body_desc}</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Target:</b> <b>{dir_sign}{target_pct:.2f}%</b>\n"
                f"<b>Direction:</b> <b>{direction}</b>\n"
                f"<b>Prediction Window:</b> {window} minutes\n"
                f"<b>Current Price:</b> {curr_price_str}\n"
                f"<b>Model Probability:</b> <b>{prob_str}</b>\n"
                f"<b>System Threshold:</b> {thresh_str}\n\n"
                f"<b>Status:</b> <b>EARLY WARNING ACTIVE</b>\n"
                f"<b>Time:</b> {time_str}\n\n"
                f"⏳ <i>Verifying target outcome over the next {window} minutes...</i>"
            )
            short_summary = f"Early Warning: {symbol} target {dir_sign}{target_pct:.2f}% predicted to be achieved in {window}m (Prob: {prob_str})"

        elif ev == NotificationEventType.TARGET_TRIGGERED.value:
            title = f"🎯 Success: {symbol} Target Achieved!"
            plain_text = (
                f"StockPulse Target Achieved — Success!\n\n"
                f"Success! {symbol} reached the target {dir_sign}{target_pct:.2f}% within the predicted {window}-minute window!\n\n"
                f"Stock: {symbol}\n"
                f"Target: {dir_sign}{target_pct:.2f}%\n"
                f"Current Price: {curr_price_str}\n"
                f"Realized Movement: {move_sign}{movement:.2f}%\n"
                f"Direction: {direction}\n"
                f"Prediction Window: {window} minutes\n\n"
                f"Status: TARGET ACHIEVED (Prediction Confirmed — SUCCESS)\n"
                f"Time: {time_str}\n\n"
                f"This monitoring task is complete. Only this alert has stopped; other monitoring tasks remain active."
            )
            html_text = (
                f"🎯 <b>StockPulse Target Achieved — Success!</b>\n\n"
                f"<b>Success!</b> <b>{symbol}</b> reached the target <b>{dir_sign}{target_pct:.2f}%</b> within the predicted <b>{window}-minute</b> window!\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Target:</b> <b>{dir_sign}{target_pct:.2f}%</b>\n"
                f"<b>Current Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>Realized Movement:</b> <b>{move_sign}{movement:.2f}%</b>\n"
                f"<b>Direction:</b> {direction}\n"
                f"<b>Prediction Window:</b> {window} minutes\n\n"
                f"<b>Status:</b> <b>TARGET ACHIEVED — SUCCESS</b> (Prediction Confirmed)\n"
                f"<b>Time:</b> {time_str}\n\n"
                f"⏹️ <i>This monitoring task is complete. Only this alert has stopped; other monitoring tasks remain active.</i>"
            )
            short_summary = f"Success! {symbol} reached target {dir_sign}{target_pct:.2f}% within {window}m. This monitoring task is complete."

        elif ev == NotificationEventType.HALFWAY_MILESTONE.value:
            title = f"⚡ Halfway Milestone: {symbol} 50% Covered!"
            plain_text = (
                f"StockPulse Momentum Milestone\n\n"
                f"Halfway to Target! {symbol} has covered 50% of the target distance towards {dir_sign}{target_pct:.2f}%.\n\n"
                f"Stock: {symbol}\n"
                f"Current Price: {curr_price_str}\n"
                f"Current Movement: {move_sign}{movement:.2f}% (Target: {dir_sign}{target_pct:.2f}%)\n"
                f"Time Window: {window} minutes\n\n"
                f"Status: MOMENTUM MILESTONE (50% Reached)\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"⚡ <b>StockPulse Momentum Milestone (50%)</b>\n\n"
                f"<b>Halfway to Target!</b> <b>{symbol}</b> has covered <b>50%</b> of the target distance towards <b>{dir_sign}{target_pct:.2f}%</b>.\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Current Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>Current Movement:</b> <b>{move_sign}{movement:.2f}%</b> (Target: {dir_sign}{target_pct:.2f}%)\n"
                f"<b>Prediction Horizon:</b> {window} minutes\n\n"
                f"<b>Status:</b> <i>50% Milestone Reached — Momentum Strong</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"50% Milestone: {symbol} moved {move_sign}{movement:.2f}% towards target {dir_sign}{target_pct:.2f}%"

        elif ev == NotificationEventType.STOP_LOSS_TRIGGERED.value:
            sl_pct = float(data.get("stop_loss_percent") or 0.03)
            title = f"🛡️ Stop-Loss Triggered: {symbol}"
            plain_text = (
                f"StockPulse Protective Stop-Loss Triggered\n\n"
                f"Risk Protection Alert: {symbol} has dropped to your stop-loss boundary (-{sl_pct:.2f}%).\n\n"
                f"Stock: {symbol}\n"
                f"Current Price: {curr_price_str}\n"
                f"Realized Drop: {move_sign}{movement:.2f}%\n"
                f"Stop-Loss Level: -{sl_pct:.2f}%\n"
                f"Baseline Entry: {start_price_str}\n\n"
                f"Status: STOP-LOSS TRIGGERED (Capital Protected)\n"
                f"Time: {time_str}\n\n"
                f"Monitoring for this alert has stopped to protect your capital."
            )
            html_text = (
                f"🛡️ <b>StockPulse Protective Stop-Loss Triggered</b>\n\n"
                f"<b>Risk Protection Alert:</b> <b>{symbol}</b> has dropped to your stop-loss boundary (<b>-{sl_pct:.2f}%</b>).\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Current Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>Realized Movement:</b> <span style='color: #dc2626;'><b>{move_sign}{movement:.2f}%</b></span>\n"
                f"<b>Stop-Loss Level:</b> -{sl_pct:.2f}%\n"
                f"<b>Baseline Entry:</b> {start_price_str}\n\n"
                f"<b>Status:</b> <b style='color: #dc2626;'>STOP-LOSS TRIGGERED</b> (Capital Protected)\n"
                f"<b>Time:</b> {time_str}\n\n"
                f"🛑 <i>Monitoring for this alert has stopped to protect capital.</i>"
            )
            short_summary = f"Stop-Loss: {symbol} hit protective stop-loss (-{sl_pct:.2f}%). Monitoring stopped."

        elif ev in (NotificationEventType.PREDICTION_FAILED.value, NotificationEventType.MONITORING_EXPIRED.value):
            title = f"⚠️ Prediction Failed: {symbol} Target Not Reached"
            plain_text = (
                f"StockPulse Prediction Failed\n\n"
                f"The model failed to predict the right value. {symbol} did not reach target {dir_sign}{target_pct:.2f}% within the predicted {window}-minute window.\n\n"
                f"Stock: {symbol}\n"
                f"Target: {dir_sign}{target_pct:.2f}%\n"
                f"Current Price: {curr_price_str}\n"
                f"Realized Movement: {move_sign}{movement:.2f}%\n"
                f"Direction: {direction}\n"
                f"Prediction Horizon: {window} minutes\n\n"
                f"Status: PREDICTION FAILED (Target Not Reached)\n"
                f"Time: {time_str}\n\n"
                f"Continuous monitoring remains active."
            )
            html_text = (
                f"⚠️ <b>StockPulse Prediction Failed</b>\n\n"
                f"<b>The model failed to predict the right value.</b> <b>{symbol}</b> did not reach target <b>{dir_sign}{target_pct:.2f}%</b> within the predicted <b>{window}-minute</b> window.\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Target:</b> <b>{dir_sign}{target_pct:.2f}%</b>\n"
                f"<b>Current Price:</b> {curr_price_str}\n"
                f"<b>Realized Movement:</b> {move_sign}{movement:.2f}%\n"
                f"<b>Direction:</b> {direction}\n"
                f"<b>Prediction Horizon:</b> {window} minutes\n\n"
                f"<b>Status:</b> <i>PREDICTION FAILED (Target Not Reached)</i>\n"
                f"<b>Time:</b> {time_str}\n\n"
                f"🔄 <i>Continuous monitoring remains active for next cycle.</i>"
            )
            short_summary = f"The model failed to predict the right value: {symbol} did not reach {dir_sign}{target_pct:.2f}% in {window}m. Continuous monitoring active."

        elif ev == NotificationEventType.MONITORING_STOPPED.value:
            title = f"StockPulse Monitoring Stopped: {symbol}"
            plain_text = (
                f"StockPulse Monitoring Stopped\n\n"
                f"{symbol} monitoring has been stopped.\n\n"
                f"Target: {dir_sign}{target_pct:.2f}%\n"
                f"Window: {window} minutes\n\n"
                f"Status: STOPPED"
            )
            html_text = (
                f"⏹️ <b>StockPulse Monitoring Stopped</b>\n\n"
                f"<b>{symbol}</b> monitoring has been stopped.\n\n"
                f"<b>Target:</b> {dir_sign}{target_pct:.2f}%\n"
                f"<b>Window:</b> {window} minutes\n\n"
                f"<b>Status:</b> <i>STOPPED</i>"
            )
            short_summary = f"{symbol} monitoring stopped by user"

        elif ev == NotificationEventType.DATA_UNAVAILABLE.value:
            title = f"StockPulse Data Warning: {symbol}"
            plain_text = (
                f"StockPulse Data Warning\n\n"
                f"Live market data for {symbol} is currently unavailable.\n\n"
                f"Monitoring has been temporarily paused.\n\n"
                f"Status: DATA UNAVAILABLE"
            )
            html_text = (
                f"⚠️ <b>StockPulse Data Warning</b>\n\n"
                f"Live market data for <b>{symbol}</b> is currently unavailable.\n\n"
                f"Monitoring has been temporarily paused.\n\n"
                f"<b>Status:</b> <b>DATA UNAVAILABLE</b>"
            )
            short_summary = f"Live market data unavailable for {symbol}; monitoring paused"

        elif ev == NotificationEventType.MODEL_UNAVAILABLE.value:
            title = f"StockPulse System Warning: {symbol}"
            plain_text = (
                f"StockPulse System Warning\n\n"
                f"The prediction model is currently unavailable for {symbol}.\n\n"
                f"ML-based monitoring has been paused.\n\n"
                f"Status: MODEL UNAVAILABLE"
            )
            html_text = (
                f"⚠️ <b>StockPulse System Warning</b>\n\n"
                f"The prediction model is currently unavailable for <b>{symbol}</b>.\n\n"
                f"ML-based monitoring has been paused.\n\n"
                f"<b>Status:</b> <b>MODEL UNAVAILABLE</b>"
            )
            short_summary = f"ML model unavailable for {symbol}; monitoring paused"

        elif ev == NotificationEventType.SUPERTREND_APPROACHING.value:
            st_price = data.get("supertrend_price")
            st_str = f"₹{st_price:,.2f}" if st_price is not None else "N/A"
            dist_pts = float(data.get("distance_points") or 0.0)
            prox_limit = float(data.get("proximity_threshold") or 1.0)
            curr_dir = str(data.get("current_direction") or "ACTIVE").upper()
            target_dir = str(data.get("target_direction") or ("BUY" if curr_dir == "SELL" else "SELL")).upper()
            prob_val = data.get("model_probability")
            prob_str = f"{float(prob_val) * 100:.1f}%" if prob_val is not None else "High Conviction"

            title = f"⚡ SuperIndicator Early Warning: {symbol} predicted to hit within {dist_pts:.2f} pts!"
            plain_text = (
                f"SuperIndicator Early Warning — {symbol}\n\n"
                f"Stock: {symbol}\n"
                f"Live Price: {curr_price_str}\n"
                f"SuperIndicator Line: {st_str}\n"
                f"Distance to Hit: {dist_pts:.2f} points (Target: {prox_limit:.1f} pts)\n"
                f"Current Situation: {curr_dir}\n"
                f"Predicted Trend Change: {target_dir}\n"
                f"Model Probability: {prob_str}\n\n"
                f"Status: Model predicts SuperIndicator will HIT and change to {target_dir} within your {prox_limit:.1f} points window!\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"⚡ <b>SuperIndicator Early Warning: {symbol}</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Live Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>SuperIndicator Line:</b> <b>{st_str}</b>\n"
                f"<b>Gap to Hit:</b> <span style='color: #f59e0b; font-weight: 700;'>{dist_pts:.2f} points</span> (Target: ≤ {prox_limit:.1f} pts)\n"
                f"<b>Current Situation:</b> <b>{curr_dir}</b>\n"
                f"<b>Predicted Change:</b> <b style='color: {'#16a34a' if target_dir == 'BUY' else '#dc2626'};'>{target_dir}</b> (Model: {prob_str})\n\n"
                f"<i>Model predicts SuperIndicator hit within your {prox_limit:.1f} points window!</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"{symbol} SuperIndicator Early Warning: model predicts hit within {dist_pts:.2f} pts to {target_dir} ({prob_str})"

        elif ev == NotificationEventType.SUPERTREND_BUY.value:
            st_price = data.get("supertrend_price")
            st_str = f"₹{st_price:,.2f}" if st_price is not None else "N/A"
            prob_val = data.get("model_probability")
            prob_str = f"{float(prob_val) * 100:.1f}%" if prob_val is not None else None

            title = f"🎯 SuperIndicator Hit: {symbol} — BUY Confirmed!"
            model_line = f"\nModel Conviction: {prob_str}" if prob_str else ""
            model_html = f"\n<b>Model Prediction:</b> <b style='color: #16a34a;'>TRUE ({prob_str})</b>\n" if prob_str else ""

            plain_text = (
                f"SuperIndicator Hit — BUY Confirmed\n\n"
                f"Stock: {symbol}\n"
                f"Status: SuperIndicator Hit (Model Prediction is TRUE!)\n"
                f"Current Situation: BUY 🚀\n"
                f"Trigger Price: {curr_price_str}\n"
                f"SuperIndicator Line: {st_str}{model_line}\n\n"
                f"Status: SuperIndicator line hit and penetrated. Trend confirmed BUY!\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"🎯 <b>SuperIndicator Hit: {symbol}</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Status:</b> <span style='color: #16a34a; font-weight: 700;'>Model Prediction is TRUE!</span>\n"
                f"<b>Current Situation:</b> <b style='color: #16a34a;'>BUY</b> 🚀\n"
                f"<b>Trigger Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>SuperIndicator Support:</b> <b>{st_str}</b>\n"
                f"{model_html}\n"
                f"<i>SuperIndicator line hit and penetrated. Trend confirmed BUY!</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"SuperIndicator Hit: {symbol} confirmed BUY at {curr_price_str} (Model Prediction is TRUE)"

        elif ev == NotificationEventType.SUPERTREND_SELL.value:
            st_price = data.get("supertrend_price")
            st_str = f"₹{st_price:,.2f}" if st_price is not None else "N/A"
            prob_val = data.get("model_probability")
            prob_str = f"{float(prob_val) * 100:.1f}%" if prob_val is not None else None

            title = f"🎯 SuperIndicator Hit: {symbol} — SELL Confirmed!"
            model_line = f"\nModel Conviction: {prob_str}" if prob_str else ""
            model_html = f"\n<b>Model Prediction:</b> <b style='color: #dc2626;'>TRUE ({prob_str})</b>\n" if prob_str else ""

            plain_text = (
                f"SuperIndicator Hit — SELL Confirmed\n\n"
                f"Stock: {symbol}\n"
                f"Status: SuperIndicator Hit (Model Prediction is TRUE!)\n"
                f"Current Situation: SELL 🔻\n"
                f"Trigger Price: {curr_price_str}\n"
                f"SuperIndicator Line: {st_str}{model_line}\n\n"
                f"Status: SuperIndicator line hit and penetrated. Trend confirmed SELL!\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"🎯 <b>SuperIndicator Hit: {symbol}</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Status:</b> <span style='color: #dc2626; font-weight: 700;'>Model Prediction is TRUE!</span>\n"
                f"<b>Current Situation:</b> <b style='color: #dc2626;'>SELL</b> 🔻\n"
                f"<b>Trigger Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>SuperIndicator Resistance:</b> <b>{st_str}</b>\n"
                f"{model_html}\n"
                f"<i>SuperIndicator line hit and penetrated. Trend confirmed SELL!</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"SuperIndicator Hit: {symbol} confirmed SELL at {curr_price_str} (Model Prediction is TRUE)"

        elif ev == NotificationEventType.SUPERTREND_FALSE.value:
            st_price = data.get("supertrend_price")
            st_str = f"₹{st_price:,.2f}" if st_price is not None else "N/A"
            curr_dir = str(data.get("current_direction") or "ACTIVE").upper()

            title = f"❌ SuperIndicator: Model Prediction is False for {symbol}"
            plain_text = (
                f"SuperIndicator — Model Prediction is FALSE\n\n"
                f"Stock: {symbol}\n"
                f"Status: Model Prediction is FALSE (Did not hit SuperIndicator)\n"
                f"Current Situation: {curr_dir}\n"
                f"Current Price: {curr_price_str}\n"
                f"SuperIndicator Line: {st_str}\n\n"
                f"Status: Price bounced away without hitting SuperIndicator. Current situation remains {curr_dir}.\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"❌ <b>SuperIndicator: Model Prediction is False</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Status:</b> <span style='color: #dc2626; font-weight: 700;'>Model Prediction is FALSE</span> (Did not hit)\n"
                f"<b>Current Situation:</b> <b>{curr_dir}</b>\n"
                f"<b>Current Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>SuperIndicator Line:</b> <b>{st_str}</b>\n\n"
                f"<i>Price pulled away without hitting SuperIndicator line. Current situation remains {curr_dir}.</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"{symbol} SuperIndicator: Model Prediction is FALSE. Current situation remains {curr_dir} at {curr_price_str}"

        elif ev == NotificationEventType.SUPERTREND_CHANGED.value:
            st_price = data.get("supertrend_price")
            st_str = f"₹{st_price:,.2f}" if st_price is not None else "N/A"
            prev_color = str(data.get("previous_color") or "GREEN").upper()
            curr_color = str(data.get("current_color") or "RED").upper()
            prev_pts = float(data.get("previous_points") or 0.0)
            curr_pts = float(data.get("current_points") or 0.0)
            prev_dir = str(data.get("previous_direction") or "BUY").upper()
            curr_dir = str(data.get("current_direction") or "SELL").upper()
            change_reason = str(data.get("change_reason") or "Candle color & points shifted during formation")

            title = f"🔄 SuperIndicator Change Alert: {symbol} shifted to {curr_color} ({curr_pts:+.2f} pts)!"
            plain_text = (
                f"SuperIndicator Shift Alert — {symbol}\n\n"
                f"Stock: {symbol}\n"
                f"Status: Candle Size & Direction Changed\n"
                f"Previous: {prev_color} ({prev_pts:+.2f} pts, {prev_dir})\n"
                f"New State: {curr_color} ({curr_pts:+.2f} pts, {curr_dir})\n"
                f"Live Price: {curr_price_str}\n"
                f"SuperIndicator Line: {st_str}\n"
                f"Notice: {change_reason}\n"
                f"Time: {time_str}"
            )
            html_text = (
                f"🔄 <b>SuperIndicator Change Alert: {symbol}</b>\n\n"
                f"<b>Stock:</b> <b>{symbol}</b>\n"
                f"<b>Status:</b> <span style='color: #2563eb; font-weight: 700;'>Candle & Points Shifted</span>\n"
                f"<b>Previous State:</b> {prev_color} ({prev_pts:+.2f} pts, {prev_dir})\n"
                f"<b>Current State:</b> <b style='color: {'#16a34a' if curr_color == 'GREEN' else '#dc2626'};'>{curr_color} ({curr_pts:+.2f} pts, {curr_dir})</b>\n"
                f"<b>Live Price:</b> <b>{curr_price_str}</b>\n"
                f"<b>SuperIndicator Line:</b> <b>{st_str}</b>\n\n"
                f"<i>Notice: {change_reason}</i>\n"
                f"<b>Time:</b> {time_str}"
            )
            short_summary = f"{symbol} SuperIndicator Change: shifted from {prev_color} ({prev_pts:+.2f}) to {curr_color} ({curr_pts:+.2f})"

        else:
            # Fallback / Generic / Custom System Notifications
            title = data.get("title") or f"StockPulse Notification: {symbol}"
            plain_text = data.get("message") or data.get("plain_text") or f"StockPulse Notification for {symbol}: {ev}\n\nStatus: {ev}"
            html_text = data.get("html") or f"🔔 <b>{title}</b>\n\n{plain_text}"
            short_summary = data.get("short_summary") or f"{symbol}: {title}"

        return {
            "title": title,
            "plain_text": plain_text,
            "html": html_text,
            "short_summary": short_summary
        }
