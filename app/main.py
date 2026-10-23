import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

# Load environment variables from .env file immediately
load_dotenv()

from app.database.database import (
    engine,
    Base,
    SessionLocal,
    BASE_DIR,
    get_database_file_path,
    checkpoint_db,
    run_db_migrations,
)
from app.api.stocks import router as stocks_router, seed_default_stocks_if_needed
from app.api.alerts import router as alerts_router
from app.api.history import router as history_router
from app.api.system import router as system_router
from app.api.notifications import router as notifications_router
from app.api.auth import router as auth_router, seed_default_user_if_needed
from app.services.scheduler import start_scheduler, stop_scheduler
import threading


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup:
    active_db_file = get_database_file_path()
    print(f"[StockPulse] Initializing database at: {active_db_file}...")
    os.makedirs(os.path.dirname(active_db_file), exist_ok=True)

    Base.metadata.create_all(bind=engine)
    run_db_migrations(engine)
    
    # Seed stocks & default testing user
    db = SessionLocal()
    try:
        seed_default_stocks_if_needed(db)
        seed_default_user_if_needed(db)
    finally:
        db.close()
        
    # Pre-warm ML models and market data cache asynchronously in background to avoid blocking server readiness
    def _background_prewarm():
        try:
            from app.services.prediction import prewarm_models
            diag = prewarm_models()
            print(f"[StockPulse] Pre-warmed ML models: Target={diag['target_model']['status']} ({diag['target_model']['load_duration_ms']}ms), Reversal={diag['reversal_model']['status']} ({diag['reversal_model']['load_duration_ms']}ms) in {diag['total_prewarm_duration_ms']}ms")
        except Exception as e:
            print(f"[StockPulse] Model pre-warming warning: {e}")
        try:
            from app.services.market_data import warmup_popular_stocks
            warmup_popular_stocks()
        except Exception as e:
            print(f"[StockPulse] Warmup note: {e}")

    threading.Thread(target=_background_prewarm, daemon=True, name="StockPulsePrewarmThread").start()

    # Start continuous background monitoring scheduler
    start_scheduler(interval_seconds=10)
    print("[StockPulse] Background monitoring scheduler active (10s interval).")
    
    yield
    
    # Shutdown:
    print("[StockPulse] Shutting down scheduler...")
    stop_scheduler()

    # Ensure SQLite WAL log is cleanly flushed into main stockpulse.db file
    try:
        checkpoint_db(engine)
        print("[StockPulse] SQLite WAL checkpointed successfully on shutdown.")
    except Exception as e:
        print(f"[StockPulse] Shutdown WAL checkpoint note: {e}")


app = FastAPI(
    title="StockPulse — Dynamic Stock Market Early-Warning System",
    description="Real-time stock market monitoring and probabilistic early-warning notification engine.",
    version="1.0.0",
    lifespan=lifespan
)

# Compression & CORS Middleware
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers under /api
app.include_router(stocks_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.include_router(history_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

# Also include directly at root to satisfy spec endpoints (GET /stocks, POST /alerts, GET /alerts/history, GET /notifications)
app.include_router(stocks_router)
app.include_router(alerts_router)
app.include_router(history_router, prefix="/alerts")
app.include_router(auth_router)


# Frontend Static Files with Cache-Control headers
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        return response

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
os.makedirs(FRONTEND_DIR, exist_ok=True)
app.mount("/static", CachedStaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/sw.js")
def serve_service_worker():
    """Serve the root-scoped service worker for desktop notifications."""
    return FileResponse(os.path.join(FRONTEND_DIR, "sw.js"), media_type="application/javascript")


@app.get("/favicon.ico")
def serve_favicon():
    """Serve the application favicon."""
    return FileResponse(os.path.join(FRONTEND_DIR, "favicon.ico"), media_type="image/x-icon")


@app.get("/favicon.svg")
def serve_favicon_svg():
    """Serve the application SVG favicon."""
    return FileResponse(os.path.join(FRONTEND_DIR, "favicon.svg"), media_type="image/svg+xml")


@app.get("/favicon.png")
def serve_favicon_png():
    """Serve the application PNG favicon."""
    return FileResponse(os.path.join(FRONTEND_DIR, "favicon.png"), media_type="image/png")


@app.get("/")
def serve_dashboard():
    """Serve the main StockPulse web dashboard (Live Market & Charts)."""
    index_path = os.path.join(FRONTEND_DIR, "live.html")
    if not os.path.exists(index_path):
        index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "StockPulse API is running. Frontend not yet found."}


@app.get("/login")
@app.get("/register")
@app.get("/signin")
def serve_login_page():
    """Serve the dedicated StockPulse Login & Account Creation page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))


@app.get("/live")
def serve_live_page():
    """Serve the dedicated Live Market & Price Chart page."""
    p = os.path.join(FRONTEND_DIR, "live.html")
    if not os.path.exists(p):
        p = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(p)


@app.get("/monitoring")
def serve_monitoring_page():
    """Serve the dedicated Dynamic Monitoring & Alerts Configuration page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "monitoring.html"))


@app.get("/details")
@app.get("/stock-info")
def serve_details_page():
    """Serve the dedicated Stock Details & Key Market Statistics page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "details.html"))


@app.get("/technical")
def redirect_technical_page():
    """Redirect removed technical page to live market."""
    return RedirectResponse(url="/live", status_code=307)


@app.get("/dashboard")
def redirect_dashboard():
    """Redirect legacy dashboard to live market."""
    return RedirectResponse(url="/live", status_code=307)


@app.get("/history")
@app.get("/alert-history")
def serve_history_page():
    """Serve the dedicated Alert History & Outcome Verification page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "history.html"))


@app.get("/notifications")
@app.get("/notifications-view")
@app.get("/notifications-page")
def serve_notifications_page():
    """Serve the dedicated Real-time System Notifications page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "notifications.html"))


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "StockPulse"}
