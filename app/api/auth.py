import os
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.database.database import get_db
from app.database.models import User, Alert

router = APIRouter(prefix="/auth", tags=["Authentication"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "stockpulse-jwt-super-secret-key-2025")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hashes a raw password string using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against the stored bcrypt hash."""
    if not hashed_password or not plain_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Creates a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodes and validates a signed JWT access token."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


def get_token_from_request(request: Request, creds: Optional[HTTPAuthorizationCredentials] = None) -> Optional[str]:
    """Extracts bearer token from Authorization header, Cookie, or Query parameter."""
    if creds and creds.credentials:
        return creds.credentials
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    cookie_token = request.cookies.get("stockpulse_jwt_token")
    if cookie_token:
        return cookie_token.strip()
    query_token = request.query_params.get("token")
    if query_token:
        return query_token.strip()
    return None


def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    FastAPI dependency requiring an authenticated user.
    Raises 401 Unauthorized if token is missing, expired, or invalid.
    """
    token = get_token_from_request(request, creds)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    user_id = payload.get("sub")
    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    user = db.query(User).filter(User.id == user_id_int).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return user


def get_optional_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """FastAPI dependency for optionally resolving an authenticated user."""
    token = get_token_from_request(request, creds)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        user_id_int = int(payload.get("sub"))
        return db.query(User).filter(User.id == user_id_int).first()
    except Exception:
        return None


# --- SCHEMAS ---

class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Full name of user")
    email: str = Field(..., description="Unique email address")
    password: str = Field(..., min_length=4, max_length=128, description="Password")
    username: Optional[str] = Field(None, max_length=50, description="Optional chosen username")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError("Please provide a valid email address (e.g. user@example.com)")
        return s

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be empty")
        return s


class LoginRequest(BaseModel):
    identifier: str = Field(..., description="Email address or username")
    password: str = Field(..., description="Account password")

    @field_validator("identifier")
    @classmethod
    def clean_identifier(cls, v: str) -> str:
        s = v.strip().lower()
        if not s:
            raise ValueError("Email or username is required")
        return s


# --- ROUTE HANDLERS ---

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(req: RegisterRequest, db: Session = Depends(get_db)):
    """
    Registers a brand new user account with hashed password and generates a JWT session.
    """
    clean_email = req.email.strip().lower()
    clean_name = req.name.strip()
    
    # Check if email is already taken
    existing_by_email = db.query(User).filter(func.lower(User.email) == clean_email).first()
    if existing_by_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists. Please sign in."
        )

    # Process username
    if req.username and req.username.strip():
        clean_username = req.username.strip().lower()
        existing_by_user = db.query(User).filter(func.lower(User.username) == clean_username).first()
        if existing_by_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This username is already taken. Please choose a different username."
            )
    else:
        # Generate default username from email prefix
        base_user = clean_email.split("@")[0].lower()
        candidate = base_user
        idx = 1
        while db.query(User).filter(func.lower(User.username) == candidate).first():
            candidate = f"{base_user}_{idx}"
            idx += 1
        clean_username = candidate

    hashed_pw = hash_password(req.password)

    new_user = User(
        name=clean_name,
        username=clean_username,
        email=clean_email,
        password_hash=hashed_pw,
        created_at=datetime.utcnow()
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token({
        "sub": str(new_user.id),
        "email": new_user.email,
        "username": new_user.username,
        "name": new_user.name
    })

    return {
        "status": "SUCCESS",
        "message": f"Account created successfully for {new_user.name}!",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": new_user.id,
            "name": new_user.name,
            "username": new_user.username,
            "email": new_user.email,
            "created_at": new_user.created_at.isoformat(),
            "active_alerts_count": 0
        }
    }


@router.post("/login", status_code=status.HTTP_200_OK)
def login_user(req: LoginRequest, db: Session = Depends(get_db)):
    """
    Signs in an existing user using either their email or username and password.
    """
    ident = req.identifier.strip().lower()

    user = db.query(User).filter(
        or_(func.lower(User.email) == ident, func.lower(User.username) == ident)
    ).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password."
        )

    # Verify password
    if not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password."
        )

    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "username": user.username or "",
        "name": user.name
    })

    active_count = db.query(Alert).filter(Alert.user_id == user.id, Alert.status == "MONITORING").count()

    return {
        "status": "SUCCESS",
        "message": f"Welcome back, {user.name}!",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
            "active_alerts_count": active_count
        }
    }


@router.get("/me")
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns the authenticated user's profile and active alert statistics.
    """
    active_count = db.query(Alert).filter(Alert.user_id == current_user.id, Alert.status == "MONITORING").count()
    return {
        "logged_in": True,
        "user": {
            "id": current_user.id,
            "name": current_user.name,
            "username": current_user.username,
            "email": current_user.email,
            "created_at": current_user.created_at.isoformat(),
            "active_alerts_count": active_count
        }
    }


@router.post("/logout")
def logout_user():
    """Sign out endpoint."""
    return {"status": "SUCCESS", "message": "Signed out successfully"}
