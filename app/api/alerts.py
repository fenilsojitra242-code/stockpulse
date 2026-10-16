from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

from app.database.database import get_db
from app.database.models import Alert, Stock, User, AlertHistory, Notification
from app.schemas.alert import (
    AlertCreate,
    AlertUpdate,
    AlertResponse,
    PredictRequest,
    PredictResponse,
    TargetValidationRequest,
    TargetValidationResponse
)
from app.schemas.notification import NotificationResponse
from app.services.prediction import predict_target_probability
from app.services.market_data import get_latest_market_snapshot
from app.services.alert_engine import stop_alert_monitoring
from app.services.scheduler import trigger_immediate_evaluation
from app.api.auth import get_current_user

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def create_alert(
    alert_in: AlertCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Configure a new early-warning alert.
    Validates dynamic percentage, warning window, direction, and stock symbol.
    """
    sym = alert_in.symbol.upper()
    stock = db.query(Stock).filter(Stock.symbol == sym).first()
    if not stock:
        stock = Stock(symbol=sym, name=f"{sym} Equity", company_name=f"{sym} Equity", exchange="NSE", is_active=True)
        db.add(stock)
        db.commit()
        db.refresh(stock)
        
    now = datetime.utcnow()
    # Resolve automatic adaptive threshold
    from app.services.adaptive_threshold import adaptive_engine
    thresh_dir = "DOWN" if (alert_in.target_percent < 0 and alert_in.direction != "BOTH") else alert_in.direction
    thresh_info = adaptive_engine.get_adaptive_threshold(
        symbol=sym,
        target_percent=abs(alert_in.target_percent),
        warning_minutes=alert_in.warning_minutes,
        direction=thresh_dir
    )
    auto_threshold = thresh_info.get("threshold", 0.75) if thresh_info.get("status") == "OPTIMAL_THRESHOLD_ACTIVE" else 0.85

    from app.services.alert_engine import compute_target_price, compute_stop_loss_price
    sl_pct = alert_in.stop_loss_percent
    sl_price = alert_in.stop_loss_price
    ref_price_init = None
    target_price_init = None
    direction = thresh_dir

    try:
        from app.services.market_data import get_cached_latest_market_snapshot, get_latest_market_snapshot
        init_snap = get_cached_latest_market_snapshot(sym) or get_latest_market_snapshot(sym)
        if init_snap and init_snap.get("price"):
            ref_price_init = float(init_snap["price"])
            prev_close_init = float(init_snap.get("previous_close") or ref_price_init)
            curr_chg_init = float(init_snap.get("change_pct", 0.0) or 0.0)

            if alert_in.target_percent < 0 and direction != "BOTH":
                direction = "DOWN"
            elif direction not in ("DOWN", "BOTH"):
                direction = "UP" if alert_in.target_percent >= curr_chg_init else "DOWN"

            target_price_init = compute_target_price(prev_close_init, ref_price_init, alert_in.target_percent, direction)
            if not sl_price and sl_pct is not None:
                sl_price = compute_stop_loss_price(prev_close_init, ref_price_init, sl_pct, direction)
    except Exception as exc:
        print(f"[AlertsAPI] Price initialization notice: {exc}")

    alert = Alert(
        user_id=current_user.id,
        symbol=sym,
        stock_id=stock.id,
        direction=direction,
        target_percent=alert_in.target_percent,
        target_price=target_price_init,
        reference_price=ref_price_init,
        stop_loss_percent=sl_pct,
        stop_loss_price=sl_price,
        milestone_50_sent=False,
        warning_minutes=alert_in.warning_minutes,
        min_probability=auto_threshold,
        monitoring_interval=alert_in.monitoring_interval or 1,
        notification_method=alert_in.notification_method or "popup",
        recipient_email=alert_in.recipient_email or current_user.email,
        cooldown_minutes=alert_in.cooldown_minutes or 0,
        status="MONITORING",
        created_at=now,
        started_at=now,
        enable_supertrend=alert_in.enable_supertrend if alert_in.enable_supertrend is not None else True,
        supertrend_proximity_percent=alert_in.supertrend_proximity_percent or 0.50,
        supertrend_proximity_points=alert_in.supertrend_proximity_points or 1.0
    )
    
    db.add(alert)
    db.commit()
    db.refresh(alert)

    # Emit confirmation prediction active notification (sent to popup and dashboard feed)
    try:
        from app.services.messaging import notification_manager
        notification_manager.emit_monitoring_started(alert, db)
    except Exception as notif_err:
        print(f"[AlertsAPI] Note emitting monitoring started: {notif_err}")

    # Immediately trigger initial evaluation in background so the user gets instant feedback
    background_tasks.add_task(trigger_immediate_evaluation)

    return alert


@router.get("/active", response_model=List[AlertResponse])
def get_active_monitoring_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns all active monitoring sessions for the dedicated side panel,
    enriched with current live price, current movement, remaining %, and live probability.
    """
    from app.services.alert_engine import compute_target_price, compute_stop_loss_price
    cutoff_time = datetime.utcnow() - timedelta(minutes=30)
    active_alerts = db.query(Alert).filter(
        Alert.user_id == current_user.id,
        or_(
            Alert.status.in_(["MONITORING", "EARLY_WARNING", "TARGET_TRIGGERED", "DATA_UNAVAILABLE", "MODEL_UNAVAILABLE"]),
            and_(Alert.status == "EXPIRED", Alert.stopped_at >= cutoff_time)
        )
    ).order_by(Alert.started_at.desc()).all()
    
    unique_symbols = list(set([a.symbol for a in active_alerts]))
    snapshots = {}

    # The live page polls this endpoint every few seconds. Never perform a fresh
    # Yahoo Finance request from the polling endpoint; use the most recent cached
    # snapshot so a slow provider cannot freeze the monitoring page.
    try:
        from app.services.market_data import get_cached_latest_market_snapshot
        for sym in unique_symbols:
            snapshots[sym] = get_cached_latest_market_snapshot(sym)
    except Exception:
        snapshots = {sym: None for sym in unique_symbols}

    results = []
    for a in active_alerts:
        try:
            snap = snapshots.get(a.symbol)
            curr_p = float(snap["price"]) if (snap and snap.get("price")) else float(a.reference_price or 0.0)
            prev_close = float(snap.get("previous_close") or curr_p) if snap else curr_p
            curr_chg = float(snap.get("change_pct", 0.0) or 0.0) if snap else 0.0
            ref_p = float(a.reference_price or curr_p)

            resp = AlertResponse.from_orm(a)
            resp.current_price = curr_p
            resp.reference_price = ref_p

            if not resp.target_price:
                resp.target_price = compute_target_price(prev_close, curr_p, a.target_percent, a.direction)

            if resp.target_price and curr_p:
                resp.target_distance_rupees = round(abs(resp.target_price - curr_p), 2)
            else:
                resp.target_distance_rupees = 0.0

            # Remaining movement % towards user's day target:
            if a.direction == "DOWN":
                remaining_pct = max(0.0, curr_chg - a.target_percent)
            else:
                remaining_pct = max(0.0, a.target_percent - curr_chg)

            # Progress %
            start_chg = ((ref_p - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
            total_span = abs(a.target_percent - start_chg)
            if total_span > 0:
                covered = abs(curr_chg - start_chg)
                progress = max(0.0, min(100.0, (covered / total_span) * 100.0))
            else:
                progress = 100.0 if remaining_pct == 0 else 0.0
            resp.progress_percent = round(progress, 1)

            if not resp.stop_loss_price and a.stop_loss_percent is not None:
                resp.stop_loss_price = compute_stop_loss_price(prev_close, ref_p, a.stop_loss_percent, a.direction)

            resp.current_movement = round(curr_chg, 2)
            resp.remaining_percent = round(remaining_pct, 2)

            # SuperTrend real-time metrics
            if getattr(a, "enable_supertrend", True):
                st_p = getattr(a, "last_supertrend_price", None)
                st_dir = getattr(a, "last_supertrend_direction", None)
                prox_pct = getattr(a, "supertrend_proximity_percent", 0.50) or 0.50
                prox_pts = getattr(a, "supertrend_proximity_points", 1.0) or 1.0
                resp.enable_supertrend = True
                resp.supertrend_proximity_percent = prox_pct
                resp.supertrend_proximity_points = prox_pts
                resp.last_supertrend_direction = st_dir
                resp.last_supertrend_price = st_p
                resp.supertrend_price = st_p
                resp.supertrend_direction = st_dir
                if st_p and curr_p:
                    gap_pts = round(abs(curr_p - st_p), 2)
                    gap_pct = round((gap_pts / max(0.01, curr_p)) * 100.0, 2)
                    resp.supertrend_gap_points = gap_pts
                    resp.supertrend_gap_percent = gap_pct
                    resp.supertrend_is_approaching = bool(gap_pct <= prox_pct or gap_pts <= prox_pts)

            results.append(resp)
        except Exception:
            results.append(AlertResponse.from_orm(a))
    return results


@router.get("", response_model=List[AlertResponse])
def list_alerts(
    status_filter: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all alerts for current user, optionally filtered by status."""
    query = db.query(Alert).filter(Alert.user_id == current_user.id)
    if status_filter:
        query = query.filter(Alert.status == status_filter.upper())
    return query.order_by(Alert.created_at.desc()).all()


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get single alert details."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.user_id and alert.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to access this alert")
    return alert


@router.post("/{alert_id}/start", response_model=AlertResponse)
def start_monitoring_alert(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Resume / start active monitoring for this alert."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.user_id and alert.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to modify this alert")
        
    now = datetime.utcnow()
    alert.status = "MONITORING"
    alert.started_at = now
    alert.stopped_at = None
    alert.early_warning_sent = False
    alert.last_triggered_at = None
    alert.last_event = "MONITORING_STARTED"
    # A restarted alert begins a fresh monitoring cycle from the current market price.
    try:
        from app.services.market_data import get_latest_market_snapshot
        snap = get_latest_market_snapshot(alert.symbol)
        if snap and snap.get("price"):
            alert.reference_price = snap["price"]
            alert.target_price = None
    except Exception:
        pass
    db.commit()
    db.refresh(alert)

    try:
        from app.services.messaging import notification_manager
        notification_manager.emit_monitoring_started(alert, db)
    except Exception as notif_err:
        print(f"[AlertsAPI] Note emitting monitoring started: {notif_err}")

    try:
        trigger_immediate_evaluation()
    except Exception:
        pass

    return alert


@router.get("/{alert_id}/notifications", response_model=List[NotificationResponse])
def get_alert_notifications(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch all notifications specifically linked to this alert session."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.user_id and alert.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to access notifications for this alert")
    items = db.query(Notification).filter(Notification.alert_id == alert_id, Notification.user_id == current_user.id).order_by(Notification.created_at.desc()).all()
    return items


@router.post("/{alert_id}/stop", response_model=AlertResponse)
def stop_monitoring_endpoint(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Stop monitoring for this alert:
    1. Stops background monitoring process.
    2. Stops sending notifications.
    3. Changes status to STOPPED.
    4. Saves the monitoring session in history with duration.
    """
    target = db.query(Alert).filter(Alert.id == alert_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Alert not found")
    if target.user_id and target.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to modify this alert")

    alert = stop_alert_monitoring(alert_id, db)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.delete("/{alert_id}")
def delete_alert(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an alert."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.user_id and alert.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to delete this alert")
    db.delete(alert)
    db.commit()
    return {"message": "Alert deleted successfully", "id": alert_id}


@router.post("/predict", response_model=PredictResponse)
def predict_target(req: PredictRequest):
    """
    On-demand prediction endpoint:
    Calculates probability, target price, prediction label, and explanation.
    """
    try:
        from app.services.alert_engine import compute_target_price
        snap = get_latest_market_snapshot(req.symbol)
        curr_price = float(snap.get("price") or 0.0)
        prev_close = float(snap.get("previous_close") or curr_price)
        curr_change = float(snap.get("change_pct", 0.0) or 0.0)

        target_price = compute_target_price(prev_close, curr_price, req.target_percent, req.direction)
        if curr_price > 0 and target_price > 0:
            dist_pct = abs((target_price - curr_price) / curr_price) * 100.0
        else:
            dist_pct = abs(req.target_percent - curr_change)
        dist_pct = max(0.01, dist_pct)

        direction = req.direction
        if req.target_percent < 0 and direction != "BOTH":
            direction = "DOWN"
        elif direction not in ("DOWN", "BOTH"):
            direction = "UP" if target_price >= curr_price else "DOWN"

        result = predict_target_probability(
            symbol=req.symbol,
            target_percent=dist_pct,
            warning_minutes=req.warning_minutes,
            direction=direction,
            market_snapshot=snap
        )
        resp_dict = {
            "symbol": result["symbol"],
            "current_price": result["current_price"],
            "current_change": result["current_change"],
            "target_percent": req.target_percent,
            "target_price": target_price,
            "target_price_up": result.get("target_price_up"),
            "target_price_down": result.get("target_price_down"),
            "warning_minutes": result["warning_minutes"],
            "direction": direction,
            "probability": result["probability"],
            "probability_up": result.get("probability_up"),
            "probability_down": result.get("probability_down"),
            "features_summary": result["features_summary"],
            "is_alert_worthy": result["is_alert_worthy"],
            "prediction_label": result["prediction_label"],
            "status_label": result["status_label"],
            "explanation": result["explanation"],
            "disclaimer": result["disclaimer"]
        }
        try:
            from app.services.feasibility import evaluate_target_feasibility
            resp_dict["feasibility"] = evaluate_target_feasibility(
                symbol=req.symbol,
                target_percent=req.target_percent,
                warning_minutes=req.warning_minutes,
                direction=direction,
                market_snapshot=snap
            )
        except Exception:
            resp_dict["feasibility"] = None

        return resp_dict
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")


@router.post("/validate_target", response_model=TargetValidationResponse)
def validate_target_endpoint(req: TargetValidationRequest):
    """
    Evaluates target feasibility by analyzing historical stock volatility and ADR.
    Returns achievability score, validation status, historical stats, and realistic recommendations.
    """
    try:
        from app.services.feasibility import evaluate_target_feasibility
        v_dir = req.direction or "UP"
        if req.target_percent < 0 and v_dir != "BOTH":
            v_dir = "DOWN"
        result = evaluate_target_feasibility(
            symbol=req.symbol,
            target_percent=req.target_percent,
            warning_minutes=req.warning_minutes or 5,
            direction=v_dir
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Target validation error: {str(e)}")


@router.post("/{alert_id}/extend", response_model=AlertResponse)
def extend_alert_window(
    alert_id: int,
    minutes: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Extend the warning window of an active alert by N minutes."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.user_id and alert.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to modify this alert")
    alert.warning_minutes = max(1, alert.warning_minutes + minutes)
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/{alert_id}/reset_ref", response_model=AlertResponse)
def reset_alert_reference(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Reset reference price to latest live market quote."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.user_id and alert.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to modify this alert")
    from app.services.market_data import get_latest_market_snapshot
    snap = get_latest_market_snapshot(alert.symbol)
    if snap and snap.get("price"):
        curr_p = float(snap["price"])
        alert.reference_price = curr_p
        pct = abs(float(alert.target_percent)) / 100.0
        if alert.direction == "DOWN":
            alert.target_price = round(curr_p * (1.0 - pct), 2)
        else:
            alert.target_price = round(curr_p * (1.0 + pct), 2)
        if alert.stop_loss_percent:
            sl_abs = abs(float(alert.stop_loss_percent)) / 100.0
            if alert.direction == "DOWN":
                alert.stop_loss_price = round(curr_p * (1.0 + sl_abs), 2)
            else:
                alert.stop_loss_price = round(curr_p * (1.0 - sl_abs), 2)
    alert.status = "MONITORING"
    alert.early_warning_sent = False
    alert.milestone_50_sent = False
    alert.last_triggered_at = None
    db.commit()
    db.refresh(alert)
    return alert
