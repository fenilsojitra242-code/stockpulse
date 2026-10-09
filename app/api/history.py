from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

from app.database.database import get_db
from app.database.models import AlertHistory
from app.schemas.alert import AlertHistoryResponse
from app.services.alert_engine import get_performance_summary

router = APIRouter(prefix="/history", tags=["Alert History"])


@router.get("", response_model=List[AlertHistoryResponse])
def get_alert_history(
    symbol: Optional[str] = None,
    result: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """Retrieve historical alerts with verified actual outcomes (SUCCESS / FAILED / STOPPED / PENDING)."""
    query = db.query(AlertHistory)
    if symbol:
        query = query.filter(AlertHistory.symbol == symbol.upper())
    if result:
        query = query.filter(AlertHistory.result == result.upper())
        
    return query.order_by(AlertHistory.triggered_at.desc()).limit(limit).all()


@router.get("/stats")
def get_history_performance_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Returns empirical performance KPIs:
    Hit rate %, True Positive vs False Alert counts, Average probability.
    """
    return get_performance_summary(db)


@router.delete("/clear")
def clear_history(db: Session = Depends(get_db)):
    """Clear all alert history records."""
    db.query(AlertHistory).delete()
    db.commit()
    return {"message": "Alert history cleared"}
