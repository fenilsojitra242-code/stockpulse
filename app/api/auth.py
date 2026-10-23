import os
import re
import time
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from app.database.database import get_db
from app.database.models import User, Alert

router = APIRouter(prefix="/auth", tags=["Authentication"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "stockpulse-jwt-super-secret-key-2025")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 365
COOKIE_NAME = "stockpulse_jwt_token"
IS_PRODUCTION = os.getenv("ENVIRONMENT", "").lower() == "production"
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true" or (
    IS_PRODUCTION and os.getenv("HOST", "") not in ("127.0.0.1", "localhost")
)

security = HTTPBearer(auto_error=False)

# Failed login attempt rate limiting (in-memory sliding window)
FAILED_LOGIN_ATTEMPTS: Dict[str, List[float]] = {}
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_FAILED_ATTEMPTS = 5


def check_login_rate_limit(client_key: str):
    now = time.time()
    attempts = [t for t in FAILED_LOGIN_ATTEMPTS.get(client_key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    FAILED_LOGIN_ATTEMPTS[client_key] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - attempts[0]))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Please wait {max(1, retry_after)} seconds before trying again."
        )


def record_failed_login(client_key: str):
    now = time.time()
    attempts = [t for t in FAILED_LOGIN_ATTEMPTS.get(client_key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)
    FAILED_LOGIN_ATTEMPTS[client_key] = attempts


def clear_failed_login(client_key: str):
    FAILED_LOGIN_ATTEMPTS.pop(client_key, None)


def set_auth_cookie(response: Response, token: str):
    """Sets a secure HTTP session cookie with appropriate environment attributes."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=365 * 86400,
        path="/",
        samesite="lax",
        httponly=True,
        secure=SECURE_COOKIES
    )


def clear_auth_cookie(response: Response):
    """Clears the session cookie on sign out without deleting database records."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        samesite="lax",
        httponly=True,
        secure=SECURE_COOKIES
    )


def hash_password(password: str) -> str:
    """Hashes a raw password string using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > 72:
        pw_bytes = pw_bytes[:72]
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against the stored bcrypt hash."""
    if not hashed_password or not plain_password:
        return False
    try:
        hp = hashed_password.strip() if isinstance(hashed_password, str) else hashed_password
        h_bytes = hp.encode("utf-8") if isinstance(hp, str) else hp
        p_bytes = plain_password.encode("utf-8")
        if len(p_bytes) > 72:
            p_bytes = p_bytes[:72]
        if bcrypt.checkpw(p_bytes, h_bytes):
            return True
        if plain_password.strip() != plain_password:
            p_strip = plain_password.strip().encode("utf-8")
            if len(p_strip) > 72:
                p_strip = p_strip[:72]
            if bcrypt.checkpw(p_strip, h_bytes):
                return True
        return False
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
    now_utc = datetime.now(timezone.utc)
    expire = now_utc + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "iat": now_utc})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodes and validates a signed JWT access token."""
    if not token or not isinstance(token, str):
        return None
    s = token.strip()
    if not s or s.lower() in ("undefined", "null", "none"):
        return None
    try:
        return jwt.decode(s, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


def get_token_from_request(request: Request, creds: Optional[HTTPAuthorizationCredentials] = None) -> Optional[str]:
    """Extracts bearer token from Authorization header, Cookie, or Query parameter."""
    if creds and creds.credentials:
        c = creds.credentials.strip()
        if c and c.lower() not in ("undefined", "null", "none"):
            return c
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token_val = auth_header[7:].strip()
        if token_val and token_val.lower() not in ("undefined", "null", "none"):
            return token_val
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token and cookie_token.strip() and cookie_token.strip().lower() not in ("undefined", "null", "none"):
        return cookie_token.strip()
    query_token = request.query_params.get("token")
    if query_token and query_token.strip() and query_token.strip().lower() not in ("undefined", "null", "none"):
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
    confirm_password: Optional[str] = Field(None, max_length=128, description="Password confirmation")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not s or "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError("Please provide a valid email address (e.g. user@example.com)")
        if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', s):
            raise ValueError("Please provide a valid email address format (e.g. user@example.com)")
        return s

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        s = v.strip()
        if not s or len(s) < 2:
            raise ValueError("Name must be at least 2 characters long")
        return s

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip().lower()
        if not s:
            return None
        if len(s) < 3:
            raise ValueError("Username must be at least 3 characters long")
        if not re.match(r'^[a-z0-9_.-]+$', s):
            raise ValueError("Username can only contain letters, numbers, underscores, hyphens, and dots")
        return s

    @model_validator(mode="after")
    def verify_passwords_match(self):
        if self.confirm_password is not None and self.password != self.confirm_password:
            raise ValueError("Passwords do not match. Please ensure password and confirm password are identical.")
        return self


class LoginRequest(BaseModel):
    identifier: Optional[str] = Field(None, description="Email address or username")
    email: Optional[str] = Field(None, description="Email address fallback")
    username: Optional[str] = Field(None, description="Username fallback")
    password: str = Field(..., min_length=1, description="Account password")

    @model_validator(mode="before")
    @classmethod
    def resolve_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw = data.get("identifier") or data.get("email") or data.get("username")
            if raw is not None and str(raw).strip():
                data["identifier"] = str(raw).strip()
        return data

    @model_validator(mode="after")
    def validate_identifier_present(self):
        raw = self.identifier or self.email or self.username
        if not raw or not str(raw).strip():
            raise ValueError("Email or username is required.")
        self.identifier = str(raw).strip()
        return self


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

    set_auth_cookie(response, token)

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

    set_auth_cookie(response, token)

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
    Registers a brand new user account directly and permanently stores in the database.
    Verifies that the registration commits successfully before reporting success.
    """
    clean_email = req.email.strip().lower()
    clean_name = req.name.strip()

    # Verify password confirmation matches
    if req.confirm_password is not None and req.password != req.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match. Please ensure password and confirm password are identical."
        )

    # Check if email is already taken (case-insensitive)
    existing_by_email = db.query(User).filter(
        func.lower(User.email) == clean_email
    ).first()
    if existing_by_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists. Please sign in with your email and password."
        )

    # Process and validate username
    if req.username and req.username.strip():
        clean_username = req.username.strip().lower()
        existing_by_user = db.query(User).filter(
            func.lower(User.username) == clean_username
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

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except IntegrityError as e:
        db.rollback()
        err_str = str(e).lower()
        if "username" in err_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This username is already taken. Please choose a different username."
            )
        elif "email" in err_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email already exists. Please sign in with your email and password."
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account creation failed due to duplicate information. Please choose a different username or email."
            )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while saving account. The account could not be persisted."
        )

    # Strictly verify account has been permanently persisted with an ID
    if not new_user.id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify permanent account persistence in database."
        )

    token = create_access_token({
        "sub": str(new_user.id),
        "email": new_user.email,
        "username": new_user.username,
        "name": new_user.name
    })

    set_auth_cookie(response, token)

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
            "created_at": new_user.created_at.isoformat() if new_user.created_at else None,
            "active_alerts_count": 0
        }
    }


@router.post("/login", status_code=status.HTTP_200_OK)
def login_user(req: LoginRequest, response: Response, db: Session = Depends(get_db), request: Request = None):
    """
    Signs in an existing user with email or username and password.
    Queries the persistent user database, verifies bcrypt password hash,
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

    # Rate limiting key based on IP and identifier
    client_ip = (
        request.client.host
        if (request and getattr(request, "client", None) and getattr(request.client, "host", None))
        else "local"
    )
    rate_key = f"{client_ip}:{ident_lower}"
    check_login_rate_limit(rate_key)

    # Instant authentication bypass strictly for dedicated testing credentials
    if ident_lower in ["user", "user@stockpulse.local"] and (plain_pw.strip() == "123456" or plain_pw == "123456"):
        user = db.query(User).filter(
            or_(func.lower(User.email) == "user@stockpulse.local", func.lower(User.username) == "user")
        ).first()
        if not user:
            user = seed_default_user_if_needed(db)
        clear_failed_login(rate_key)
        token = create_access_token({
            "sub": str(user.id),
            "email": user.email,
            "username": user.username or "user",
            "name": user.name
        })
        set_auth_cookie(response, token)
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

    # Exact account lookup case-insensitively by registered email OR registered username only
    user = db.query(User).filter(
        or_(
            func.lower(User.email) == ident_lower,
            func.lower(User.username) == ident_lower
        )
    ).first()

    # Safe diagnostic logging (never logs passwords, hashes, tokens, or credentials)
    try:
        backend_name = db.bind.dialect.name if (db and db.bind) else "unknown"
        if backend_name == "sqlite":
            sanitized_target = "sqlite://local"
        elif db and db.bind and hasattr(db.bind, "url") and db.bind.url:
            sanitized_target = f"{backend_name}://{db.bind.url.host or 'cloud'}/{db.bind.url.database or ''}"
        else:
            sanitized_target = backend_name
        total_db_users = db.query(User).count()
        matched = bool(user)
        has_hash = bool(user and user.password_hash)
        pw_ok = verify_password(plain_pw, user.password_hash) if (user and has_hash) else False
        print(f"[AUTH DIAGNOSTIC] DB: {backend_name} ({sanitized_target}) | Total DB Users: {total_db_users} | Lookup Matched: {matched} | Has Hash: {has_hash} | Password Verified: {pw_ok}")
    except Exception as diag_err:
        print(f"[AUTH DIAGNOSTIC LOG ERROR] {diag_err}")

    if not user or not user.password_hash:
        record_failed_login(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password. Please try again."
        )

    # Verify password securely with bcrypt
    if not verify_password(plain_pw, user.password_hash):
        record_failed_login(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password. Please try again."
        )

    # Login succeeded: clear failed rate limit counter
    clear_failed_login(rate_key)

    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "username": user.username or "",
        "name": user.name
    })

    set_auth_cookie(response, token)

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
    clear_auth_cookie(response)
    return {"status": "SUCCESS", "message": "Signed out successfully"}


@router.get("/diagnostic", status_code=status.HTTP_200_OK)
def get_auth_diagnostic(db: Session = Depends(get_db)):
    """
    Safe public diagnostic endpoint to verify production database connection and persistence.
    Never exposes passwords, hashes, tokens, or database credentials.
    """
    backend_name = db.bind.dialect.name if (db and db.bind) else "unknown"
    if backend_name == "sqlite":
        sanitized_db = "sqlite://local"
    elif db and db.bind and hasattr(db.bind, "url") and db.bind.url:
        sanitized_db = f"{backend_name}://{db.bind.url.host or 'cloud'}/{db.bind.url.database or ''}"
    else:
        sanitized_db = backend_name
    total_users = db.query(User).count()
    test_user_present = db.query(User).filter(func.lower(User.email) == "user@stockpulse.local").first() is not None
    real_users_count = db.query(User).filter(func.lower(User.email) != "user@stockpulse.local").count()
    is_db_url_set = bool(os.getenv("DATABASE_URL"))
    is_persistent = (backend_name != "sqlite") or bool(os.getenv("PERSISTENT_DATA_DIR") or os.getenv("PERSISTENT_STORAGE_DIR"))

    return {
        "status": "DIAGNOSTIC_ACTIVE",
        "database_backend": backend_name,
        "sanitized_target": sanitized_db,
        "database_url_configured": is_db_url_set,
        "is_persistent": is_persistent,
        "total_users_count": total_users,
        "real_users_count": real_users_count,
        "test_user_present": test_user_present,
        "note": "Production database health check. Real user accounts require a persistent database (PostgreSQL or persistent disk) to survive Render restarts."
    }

