from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

from app.database.database import get_db
from app.database.models import AlertHistory, User
from app.schemas.alert import AlertHistoryResponse
from app.services.alert_engine import get_performance_summary
from app.api.auth import get_current_user

router = APIRouter(prefix="/history", tags=["Alert History"])


@router.get("", response_model=List[AlertHistoryResponse])
def get_alert_history(
    symbol: Optional[str] = None,
    result: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieve historical alerts with verified actual outcomes (isolated to current user)."""
    query = db.query(AlertHistory).filter(AlertHistory.user_id == current_user.id)
    if symbol:
        query = query.filter(AlertHistory.symbol == symbol.upper())
    if result:
        query = query.filter(AlertHistory.result == result.upper())
        
    return query.order_by(AlertHistory.triggered_at.desc()).limit(limit).all()


@router.get("/stats")
def get_history_performance_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Returns empirical performance KPIs:
    Hit rate %, True Positive vs False Alert counts, Average probability.
    """
    return get_performance_summary(db, user_id=current_user.id)


@router.delete("/clear")
def clear_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Clear alert history records belonging to current user."""
    db.query(AlertHistory).filter(AlertHistory.user_id == current_user.id).delete()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database transaction failed")
    return {"message": "Alert history cleared"}
