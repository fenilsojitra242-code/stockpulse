import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import User, Alert

router = APIRouter(prefix="/auth", tags=["Authentication"])

# JWT Configuration (minimum 32 bytes to satisfy RFC 7518)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "stockpulse-enterprise-grade-secure-jwt-secret-key-2026-v1")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


# --- PASSWORD HASHING UTILITIES ---

def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using bcrypt with random salt."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    if not hashed_password or not plain_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


# --- JWT TOKEN UTILITIES ---

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=JWT_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except (jwt.PyJWTError, Exception):
        return None


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    email: Optional[str] = None,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Extracts the current user from the Authorization: Bearer <token> header,
    falling back to email query parameter for backward compatibility.
    """
    # 1. Check Bearer Token
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ", 1)[1].strip()
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            user_id = payload.get("sub")
            user = db.query(User).filter(User.id == int(user_id)).first()
            if user:
                return user

    # 2. Check query parameter fallback
    if email and email.strip():
        clean_email = email.strip().lower()
        user = db.query(User).filter(User.email == clean_email).first()
        if user:
            return user

    return None


def get_current_user_required(
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> User:
    """Ensures the caller is logged in, otherwise raises 401 Unauthorized."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in to access this feature.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return current_user


# --- REQUEST & RESPONSE SCHEMAS ---

class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Full display name")
    email: str = Field(..., description="Valid email address")
    password: str = Field(..., min_length=6, max_length=100, description="Password (min 6 chars)")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError("Please provide a valid email address (e.g. user@example.com)")
        return s

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters long")
        return v


class LoginRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: Optional[str] = Field(None, description="User password")
    name: Optional[str] = Field(None, description="Optional user display name")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError("Please provide a valid email address (e.g. user@example.com)")
        return s


def build_user_dict(user: User, db: Session) -> Dict[str, Any]:
    active_count = db.query(Alert).filter(Alert.user_id == user.id, Alert.status == "MONITORING").count()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "has_password": bool(user.password_hash),
        "created_at": user.created_at.isoformat() if user.created_at else datetime.utcnow().isoformat(),
        "active_alerts_count": active_count
    }


# --- API ROUTES ---

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """
    Register a new user account with Email and Password.
    Hashes the password securely with bcrypt and issues a signed JWT token.
    """
    clean_email = req.email.strip().lower()
    clean_name = req.name.strip()
    
    existing = db.query(User).filter(User.email == clean_email).first()
    
    if existing and existing.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists. Please sign in instead."
        )

    pwd_hash = hash_password(req.password)
    
    if existing:
        # Existing user who previously used email-only alert flow: upgrade with password
        existing.name = clean_name
        existing.password_hash = pwd_hash
        db.commit()
        db.refresh(existing)
        user = existing
    else:
        user = User(
            name=clean_name,
            email=clean_email,
            password_hash=pwd_hash,
            created_at=datetime.utcnow()
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email})
    user_info = build_user_dict(user, db)

    return {
        "status": "SUCCESS",
        "message": f"Account created! Welcome to StockPulse, {user.name}.",
        "access_token": token,
        "token_type": "bearer",
        "token": token,
        "user": user_info
    }


@router.post("/login", status_code=status.HTTP_200_OK)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """
    Sign in with Email and Password.
    Validates password against bcrypt hash and issues a signed JWT token.
    """
    clean_email = req.email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()

    if not user:
        # If user does not exist yet:
        if req.password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No account found with this email. Please create an account first."
            )
        # Legacy guest email creation
        clean_name = req.name.strip() if req.name and req.name.strip() else clean_email.split("@")[0].capitalize()
        user = User(name=clean_name, email=clean_email, created_at=datetime.utcnow())
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # If user exists and has a password:
        if user.password_hash:
            if not req.password:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Password is required for this account. Please enter your password."
                )
            if not verify_password(req.password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect password. Please verify and try again."
                )
        else:
            # User exists from legacy alert creation without a password
            if req.password:
                user.password_hash = hash_password(req.password)
                db.commit()
                db.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email})
    user_info = build_user_dict(user, db)

    return {
        "status": "SUCCESS",
        "message": f"Welcome back, {user.name}!",
        "access_token": token,
        "token_type": "bearer",
        "token": token,
        "user": user_info
    }


@router.get("/me")
def get_current_user_profile(
    authorization: Optional[str] = Header(None),
    email: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Retrieve profile for the currently authenticated user session.
    Reads Bearer token from Authorization header or falls back to email.
    """
    user = get_current_user_optional(authorization=authorization, email=email, db=db)
    
    if not user:
        return {"logged_in": False, "user": None}

    user_info = build_user_dict(user, db)
    return {
        "logged_in": True,
        "user": user_info
    }


@router.post("/logout")
def logout():
    """Sign out and invalidate user session."""
    return {"status": "SUCCESS", "message": "Signed out successfully"}
