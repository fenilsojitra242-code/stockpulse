from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import User, Alert

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: str = Field(..., description="User email address for receiving alert messages")
    name: Optional[str] = Field(None, description="Optional user display name")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError("Please provide a valid email address (e.g. user@example.com)")
        return s


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    created_at: datetime
    active_alerts_count: int = 0


@router.post("/login", status_code=status.HTTP_200_OK)
def login_or_register(req: LoginRequest, db: Session = Depends(get_db)):
    """
    Simple email login.
    Finds or registers the user by email, setting them as the active alert recipient.
    """
    clean_email = req.email.strip().lower()
    clean_name = req.name.strip() if req.name and req.name.strip() else clean_email.split("@")[0].capitalize()

    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        user = User(
            name=clean_name,
            email=clean_email,
            created_at=datetime.utcnow()
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Update name if new name provided
        if req.name and req.name.strip():
            user.name = clean_name
            db.commit()
            db.refresh(user)

    active_count = db.query(Alert).filter(Alert.user_id == user.id, Alert.status == "MONITORING").count()

    return {
        "status": "SUCCESS",
        "message": f"Signed in as {user.email}",
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "created_at": user.created_at.isoformat(),
        "active_alerts_count": active_count,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
            "active_alerts_count": active_count
        }
    }


@router.get("/me")
def get_current_user_profile(email: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Retrieve profile for the specified email or the most recently active user.
    """
    if email:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
    else:
        user = db.query(User).order_by(User.id.desc()).first()

    if not user:
        return {"logged_in": False, "user": None}

    active_count = db.query(Alert).filter(Alert.user_id == user.id, Alert.status == "MONITORING").count()
    return {
        "logged_in": True,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
            "active_alerts_count": active_count
        }
    }


@router.post("/logout")
def logout():
    """Sign out / clear active user session."""
    return {"status": "SUCCESS", "message": "Signed out successfully"}
