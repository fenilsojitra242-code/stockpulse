import os
import json
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional


DIAGNOSTICS_FILE = "data/logs/supertrend_diagnostics.jsonl"


class DiagnosticEventLogger:
    """
    Phase 2 Diagnostic Event Logger:
    Records every Supertrend prediction, causal feature snapshot, applied thresholds,
    market regime classification, decision reasons, and tracks subsequent outcomes.
    """

    def __init__(self, max_memory_records: int = 2000):
        self._memory_buffer = deque(maxlen=max_memory_records)
        self._pending_events: Dict[str, Dict[str, Any]] = {}
        self._ensure_log_dir()

    def _ensure_log_dir(self):
        log_dir = os.path.dirname(DIAGNOSTICS_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

    def record_prediction(
        self,
        symbol: str,
        candle_interval: str,
        prediction_timestamp: str,
        market_data_timestamp: str,
        confirmed_direction_before: str,
        raw_direction: str,
        atr: float,
        final_upper_band: float,
        final_lower_band: float,
        closing_price: float,
        features_snapshot: Dict[str, Any],
        predicted_direction: str,
        horizon_minutes: int,
        reversal_probability: float,
        heuristic_score: float,
        applied_threshold: float,
        market_regime: str,
        volatility_regime: str,
        early_warning_decision: bool,
        decision_reason: str,
        model_type: str = "CALIBRATED_ML"
    ) -> Dict[str, Any]:
        """
        Records a prediction event causally at prediction time.
        Future data is strictly prohibited from features_snapshot.
        """
        event_id = str(uuid.uuid4())[:12]
        now_utc = datetime.now(timezone.utc).isoformat()

        record = {
            "event_id": event_id,
            "logged_at": now_utc,
            "symbol": str(symbol).upper(),
            "candle_interval": str(candle_interval),
            "prediction_timestamp": str(prediction_timestamp),
            "market_data_timestamp": str(market_data_timestamp),
            "confirmed_direction_before": str(confirmed_direction_before).upper(),
            "raw_direction": str(raw_direction).upper(),
            "atr": float(atr),
            "final_upper_band": float(final_upper_band),
            "final_lower_band": float(final_lower_band),
            "closing_price": float(closing_price),
            "features_snapshot": features_snapshot or {},
            "predicted_direction": str(predicted_direction).upper(),
            "horizon_minutes": int(horizon_minutes),
            "reversal_probability": float(reversal_probability),
            "heuristic_score": float(heuristic_score),
            "applied_threshold": float(applied_threshold),
            "market_regime": str(market_regime),
            "volatility_regime": str(volatility_regime),
            "early_warning_decision": bool(early_warning_decision),
            "decision_reason": str(decision_reason),
            "model_type": str(model_type),
            "outcome": "PENDING",
            "actual_subsequent_direction": None,
            "actual_flip_occurred": None,
            "actual_lead_time_minutes": None,
            "resolution_reason": None,
            "resolved_at": None
        }

        self._memory_buffer.append(record)
        self._pending_events[event_id] = record

        # Append to persistent JSONL log
        try:
            with open(DIAGNOSTICS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"[DiagnosticLogger] Failed to write event {event_id}: {e}")

        return record

    def resolve_outcome(
        self,
        event_id: str,
        actual_flip_occurred: bool,
        actual_subsequent_direction: Optional[str] = None,
        lead_time_minutes: Optional[int] = None,
        resolution_reason: str = "HORIZON_EXPIRED"
    ) -> Optional[Dict[str, Any]]:
        """
        Resolves a pending prediction once the horizon has elapsed.
        """
        record = self._pending_events.get(event_id)
        if not record:
            # Look in memory buffer
            for r in self._memory_buffer:
                if r["event_id"] == event_id:
                    record = r
                    break

        if not record:
            return None

        warned = record["early_warning_decision"]
        if warned and actual_flip_occurred:
            outcome = "TRUE_POSITIVE"
        elif warned and not actual_flip_occurred:
            outcome = "FALSE_POSITIVE"
        elif not warned and actual_flip_occurred:
            outcome = "FALSE_NEGATIVE"
        else:
            outcome = "TRUE_NEGATIVE"

        record["outcome"] = outcome
        record["actual_flip_occurred"] = bool(actual_flip_occurred)
        record["actual_subsequent_direction"] = actual_subsequent_direction
        record["actual_lead_time_minutes"] = lead_time_minutes
        record["resolution_reason"] = resolution_reason
        record["resolved_at"] = datetime.now(timezone.utc).isoformat()

        if event_id in self._pending_events:
            del self._pending_events[event_id]

        return record

    def get_recent_events(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        outcome_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves recent diagnostic records with optional filters."""
        results = list(self._memory_buffer)
        if symbol:
            sym_u = symbol.strip().upper()
            results = [r for r in results if r.get("symbol") == sym_u]
        if outcome_filter:
            out_u = outcome_filter.strip().upper()
            results = [r for r in results if r.get("outcome") == out_u]
        return results[-limit:]

    def get_diagnostics_summary(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Calculates statistical summary of resolved predictions."""
        events = list(self._memory_buffer)
        if symbol:
            sym_u = symbol.strip().upper()
            events = [e for e in events if e.get("symbol") == sym_u]

        resolved = [e for e in events if e.get("outcome") != "PENDING"]
        pending_count = len(events) - len(resolved)

        tp = sum(1 for e in resolved if e.get("outcome") == "TRUE_POSITIVE")
        fp = sum(1 for e in resolved if e.get("outcome") == "FALSE_POSITIVE")
        fn = sum(1 for e in resolved if e.get("outcome") == "FALSE_NEGATIVE")
        tn = sum(1 for e in resolved if e.get("outcome") == "TRUE_NEGATIVE")

        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        fpr = float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0

        lead_times = [e["actual_lead_time_minutes"] for e in resolved if e.get("outcome") == "TRUE_POSITIVE" and e.get("actual_lead_time_minutes") is not None]
        avg_lead = float(sum(lead_times) / len(lead_times)) if lead_times else 0.0

        # Regime breakdown
        regime_breakdown = {}
        for r_name in ("BULLISH_TREND", "BEARISH_TREND", "SIDEWAYS_CHOPPY"):
            sub = [e for e in resolved if e.get("market_regime") == r_name]
            s_tp = sum(1 for e in sub if e.get("outcome") == "TRUE_POSITIVE")
            s_fp = sum(1 for e in sub if e.get("outcome") == "FALSE_POSITIVE")
            s_fn = sum(1 for e in sub if e.get("outcome") == "FALSE_NEGATIVE")
            s_tn = sum(1 for e in sub if e.get("outcome") == "TRUE_NEGATIVE")
            s_prec = float(s_tp / (s_tp + s_fp)) if (s_tp + s_fp) > 0 else 0.0
            s_rec = float(s_tp / (s_tp + s_fn)) if (s_tp + s_fn) > 0 else 0.0
            regime_breakdown[r_name] = {
                "total": len(sub),
                "true_positives": s_tp,
                "false_positives": s_fp,
                "precision": round(s_prec, 4),
                "recall": round(s_rec, 4)
            }

        return {
            "total_predictions": len(events),
            "pending_outcomes": pending_count,
            "resolved_outcomes": len(resolved),
            "confusion_matrix": {
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "true_negatives": tn
            },
            "metrics": {
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1_score": round(f1, 4),
                "false_positive_rate": round(fpr, 4),
                "average_lead_time_minutes": round(avg_lead, 2)
            },
            "regime_breakdown": regime_breakdown
        }

    def get_summary(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Alias for get_diagnostics_summary."""
        return self.get_diagnostics_summary(symbol=symbol)


# Global diagnostic logger singleton
diagnostic_logger = DiagnosticEventLogger()
