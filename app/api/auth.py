import os
import re
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
import secrets

from app.database.database import get_db
from app.database.models import User, Alert, EmailOTP
from app.services.email_validator import validate_email_authenticity
from app.services.email_service import send_otp_email

router = APIRouter(prefix="/auth", tags=["Authentication"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "stockpulse-jwt-super-secret-key-2025")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 365

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


def seed_default_user_if_needed(db: Session) -> User:
    """
    Ensures the fixed testing user exists in the database.
    Queries strictly by unique test email 'user@stockpulse.local'.
    Never overwrites or alters real user accounts registered with different emails.
    """
    test_user = db.query(User).filter(
        func.lower(User.email) == "user@stockpulse.local"
    ).first()

    if not test_user:
        test_user = User(
            name="user",
            username="user",
            email="user@stockpulse.local",
            password_hash=hash_password("123456"),
            is_verified=True,
            created_at=datetime.utcnow()
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)
        print("[StockPulse] Fixed test user 'user' (user@stockpulse.local) initialized.")
    else:
        needs_commit = False
        if not test_user.password_hash:
            test_user.password_hash = hash_password("123456")
            needs_commit = True
        if not test_user.username:
            test_user.username = "user"
            needs_commit = True
        if not test_user.is_verified:
            test_user.is_verified = True
            needs_commit = True
        if needs_commit:
            db.commit()
            db.refresh(test_user)
    return test_user


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
    Extracts token from Authorization header (Bearer), Cookie (stockpulse_jwt_token), or query parameter.
    Raises 401 Unauthorized if missing, expired, or invalid.
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
            detail="User account not found. Please sign in again.",
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
    identifier: Optional[str] = Field(None, description="Email address or username")
    email: Optional[str] = Field(None, description="Email address fallback")
    username: Optional[str] = Field(None, description="Username fallback")
    password: str = Field(..., min_length=1, description="Account password")

    @field_validator("identifier", mode="before")
    @classmethod
    def clean_identifier(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None


class SendOTPRequest(BaseModel):
    email: str = Field(..., description="Email address to verify")
    name: str = Field(..., min_length=2, max_length=100, description="Full name of user")
    username: Optional[str] = Field(None, max_length=50, description="Optional chosen username")

    @field_validator("email")
    @classmethod
    def clean_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s:
            raise ValueError("Valid email address is required")
        return s


class VerifyOTPRegisterRequest(BaseModel):
    email: str = Field(..., description="Email address being verified")
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    name: str = Field(..., min_length=2, max_length=100, description="Full name of user")
    password: str = Field(..., min_length=4, max_length=128, description="Account password")
    username: Optional[str] = Field(None, max_length=50, description="Optional chosen username")


class ResendOTPRequest(BaseModel):
    email: str = Field(..., description="Email address to resend OTP to")
    name: Optional[str] = Field("Trader", description="User name")


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="Registered account email address")

    @field_validator("email")
    @classmethod
    def clean_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s:
            raise ValueError("Valid email address is required")
        return s


class ResetPasswordRequest(BaseModel):
    email: str = Field(..., description="Registered account email address")
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    new_password: str = Field(..., min_length=4, max_length=128, description="New password")

    @field_validator("email")
    @classmethod
    def clean_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s:
            raise ValueError("Valid email address is required")
        return s

    @field_validator("new_password")
    @classmethod
    def clean_pw(cls, v: str) -> str:
        if len(v) < 4:
            raise ValueError("Password must be at least 4 characters long")
        return v


# --- ROUTE HANDLERS ---

@router.post("/send-registration-otp")
def send_registration_otp(req: SendOTPRequest, db: Session = Depends(get_db)):
    """
    Testing stage: OTP requirement is disabled. Returns instant success.
    """
    clean_email = req.email.strip().lower()
    return {
        "status": "success",
        "message": "OTP system disabled for testing stage. You can register directly or log in using user / 123456.",
        "email": clean_email,
        "otp_disabled": True
    }


@router.post("/verify-registration-otp", status_code=status.HTTP_201_CREATED)
def verify_registration_otp(req: VerifyOTPRegisterRequest, response: Response, db: Session = Depends(get_db)):
    """
    Testing stage fallback: Registers or authenticates user directly without OTP verification.
    """
    clean_email = req.email.strip().lower()
    clean_name = req.name.strip() if req.name else "Trader"

    # Check if user already exists
    existing_user = db.query(User).filter(func.lower(User.email) == clean_email).first()
    if existing_user:
        user = existing_user
    else:
        # Process username
        if req.username and req.username.strip():
            clean_username = req.username.strip().lower()
            existing_by_user = db.query(User).filter(func.lower(User.username) == clean_username).first()
            if existing_by_user:
                clean_username = f"{clean_username}_{int(datetime.utcnow().timestamp())}"
        else:
            base_user = clean_email.split("@")[0].lower()
            clean_username = base_user
            idx = 1
            while db.query(User).filter(func.lower(User.username) == clean_username).first():
                clean_username = f"{base_user}_{idx}"
                idx += 1

        hashed_pw = hash_password(req.password or "123456")
        user = User(
            name=clean_name,
            username=clean_username,
            email=clean_email,
            password_hash=hashed_pw,
            is_verified=True,
            created_at=datetime.utcnow()
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "username": user.username or "",
        "name": user.name
    })

    response.set_cookie(
        key="stockpulse_jwt_token",
        value=token,
        max_age=365 * 86400,
        path="/",
        samesite="lax",
        httponly=False
    )

    return {
        "status": "SUCCESS",
        "message": f"Welcome, {user.name}! (Testing stage - OTP bypassed)",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
            "active_alerts_count": 0
        }
    }


@router.post("/resend-otp")
def resend_registration_otp(req: ResendOTPRequest, db: Session = Depends(get_db)):
    """Resends a fresh 6-digit OTP code to the requested email."""
    return send_registration_otp(
        SendOTPRequest(email=req.email, name=req.name or "Trader"),
        db=db
    )


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Password reset initiation (Testing stage: OTP disabled).
    """
    clean_email = req.email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == clean_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No account found with email '{clean_email}'. You can sign in using fixed test credentials: username 'user', password '123456'."
        )

    return {
        "status": "success",
        "message": "Testing mode active (OTP disabled). You can sign in using fixed test credentials: user / 123456, or reset your password directly.",
        "email": clean_email,
        "expires_in_seconds": 600
    }


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, response: Response, db: Session = Depends(get_db)):
    """
    Directly updates password for the account without requiring OTP in testing stage.
    """
    clean_email = req.email.strip().lower()

    # Look up user account
    user = db.query(User).filter(func.lower(User.email) == clean_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found."
        )

    # Hash new password and save
    user.password_hash = hash_password(req.new_password)
    db.commit()
    db.refresh(user)

    # Generate fresh access token so user is automatically signed in
    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "username": user.username or "",
        "name": user.name
    })

    response.set_cookie(
        key="stockpulse_jwt_token",
        value=token,
        max_age=365 * 86400,
        path="/",
        samesite="lax",
        httponly=False
    )

    return {
        "status": "SUCCESS",
        "message": "Password successfully updated! Welcome back.",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "active_alerts_count": 0
        }
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    """
    Registers a brand new user account directly without OTP and generates a long-lived JWT session.
    """
    clean_email = req.email.strip().lower()
    clean_name = req.name.strip()
    
    if not clean_email or "@" not in clean_email or "." not in clean_email.split("@")[-1]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid email address (e.g. user@example.com)"
        )

    # Check if email is already taken
    existing_by_email = db.query(User).filter(
        or_(User.email == clean_email, func.lower(User.email) == clean_email)
    ).first()
    if existing_by_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists. Please sign in with your email and password."
        )

    # Process username
    if req.username and req.username.strip():
        clean_username = req.username.strip().lower().replace(" ", "_")
        existing_by_user = db.query(User).filter(
            or_(User.username == clean_username, func.lower(User.username) == clean_username)
        ).first()
        if existing_by_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This username is already taken. Please choose a different username."
            )
    else:
        # Generate default username from name or email prefix
        base_user = clean_name.lower().replace(" ", "_") if clean_name else clean_email.split("@")[0].lower()
        base_user = re.sub(r'[^a-z0-9_]', '', base_user) or "user"
        candidate = base_user
        existing_rows = db.query(User.username).filter(User.username.like(f"{base_user}%")).all()
        existing_set = {r[0].lower() for r in existing_rows if r[0]}
        if candidate in existing_set:
            idx = 1
            while f"{base_user}_{idx}" in existing_set:
                idx += 1
            candidate = f"{base_user}_{idx}"
        clean_username = candidate

    hashed_pw = hash_password(req.password)

    new_user = User(
        name=clean_name,
        username=clean_username,
        email=clean_email,
        password_hash=hashed_pw,
        is_verified=True,
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

    response.set_cookie(
        key="stockpulse_jwt_token",
        value=token,
        max_age=365 * 86400,
        path="/",
        samesite="lax",
        httponly=False
    )

    return {
        "status": "SUCCESS",
        "message": f"Account created successfully for {new_user.name}! Data stored permanently.",
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
def login_user(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """
    Signs in an existing user with email/username and password.
    Queries the persistent user database, verifies the bcrypt password hash,
    and returns a signed JWT access token and session cookie.
    """
    raw_ident = req.identifier or req.email or req.username
    if not raw_ident or not raw_ident.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or username is required."
        )

    ident = raw_ident.strip()
    ident_lower = ident.lower()
    plain_pw = req.password

    # Instant authentication bypass strictly for dedicated testing credentials
    if ident_lower in ["user", "user@stockpulse.local"] and (plain_pw.strip() == "123456" or plain_pw == "123456"):
        user = db.query(User).filter(
            or_(func.lower(User.email) == "user@stockpulse.local", func.lower(User.username) == "user")
        ).first()
        if not user:
            user = seed_default_user_if_needed(db)
        token = create_access_token({
            "sub": str(user.id),
            "email": user.email,
            "username": user.username or "user",
            "name": user.name
        })
        response.set_cookie(
            key="stockpulse_jwt_token",
            value=token,
            max_age=365 * 86400,
            path="/",
            samesite="lax",
            httponly=False
        )
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
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "active_alerts_count": active_count
            }
        }

    # Lookup account case-insensitively in persistent database
    candidates = []
    if "@" in ident_lower:
        candidates = db.query(User).filter(func.lower(User.email) == ident_lower).all()
    else:
        # Match exact username first
        candidates = db.query(User).filter(func.lower(User.username) == ident_lower).all()
        if not candidates:
            # Match email prefix
            candidates = db.query(User).filter(func.lower(User.email).like(f"{ident_lower}@%")).all()
        if not candidates and len(ident_lower) >= 2:
            ident_compact = ident_lower.replace(" ", "")
            candidates = db.query(User).filter(
                or_(
                    func.lower(User.name) == ident_lower,
                    func.replace(func.lower(User.name), " ", "") == ident_compact
                )
            ).order_by(User.id.desc()).all()

    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password. Please try again."
        )

    # Verify password securely with bcrypt
    authenticated_user = None
    for candidate in candidates:
        if not candidate.password_hash:
            continue
        pw_ok = verify_password(plain_pw, candidate.password_hash)
        if not pw_ok and plain_pw.strip() != plain_pw:
            pw_ok = verify_password(plain_pw.strip(), candidate.password_hash)
        if pw_ok:
            authenticated_user = candidate
            break

    if not authenticated_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password. Please try again."
        )

    user = authenticated_user

    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "username": user.username or "",
        "name": user.name
    })

    response.set_cookie(
        key="stockpulse_jwt_token",
        value=token,
        max_age=365 * 86400,
        path="/",
        samesite="lax",
        httponly=False
    )

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
            "created_at": user.created_at.isoformat() if user.created_at else None,
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
    Protected route: rejects unauthenticated requests with 401.
    """
    active_count = db.query(Alert).filter(Alert.user_id == current_user.id, Alert.status == "MONITORING").count()
    return {
        "logged_in": True,
        "user": {
            "id": current_user.id,
            "name": current_user.name,
            "username": current_user.username,
            "email": current_user.email,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
            "active_alerts_count": active_count
        }
    }


@router.post("/logout")
def logout_user(response: Response):
    """
    Sign out endpoint: Invalidates session cookie.
    Does NOT delete the user account or user records in the database.
    """
    response.delete_cookie(key="stockpulse_jwt_token", path="/")
    return {"status": "SUCCESS", "message": "Signed out successfully"}
