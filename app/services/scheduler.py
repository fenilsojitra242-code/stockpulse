import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

from app.database.database import SessionLocal
from app.services.alert_engine import evaluate_active_alerts

_SCHEDULER: BackgroundScheduler = None
_LAST_RUN_TIME = None
_RUN_COUNT = 0
_JOB_LOCK = None


def scheduled_monitoring_job():
    """Periodic job executed by the background scheduler.

    One evaluation is allowed at a time. A slow market-provider request must
    never create a backlog of prediction jobs.
    """
    global _LAST_RUN_TIME, _RUN_COUNT, _JOB_LOCK
    if _JOB_LOCK is None:
        import threading
        _JOB_LOCK = threading.Lock()
    if not _JOB_LOCK.acquire(blocking=False):
        print("[Scheduler] Previous monitoring evaluation still running; skipping this tick.")
        return
    _LAST_RUN_TIME = datetime.utcnow()
    _RUN_COUNT += 1
    db = SessionLocal()
    try:
        results = evaluate_active_alerts(db)
        if results:
            print(f"[Scheduler] Tick #{_RUN_COUNT}: Evaluated {len(results)} alerts.")
    except Exception as e:
        print(f"[Scheduler] Error during evaluation tick: {e}")
    finally:
        db.close()
        _JOB_LOCK.release()


def start_scheduler(interval_seconds: int = 20):
    """Starts the background scheduler."""
    global _SCHEDULER
    if _SCHEDULER is not None and _SCHEDULER.running:
        print("[Scheduler] Already running.")
        return
        
    _SCHEDULER = BackgroundScheduler(
        daemon=True,
        executors={"default": ThreadPoolExecutor(max_workers=2)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 5}
    )
    _SCHEDULER.add_job(
        scheduled_monitoring_job,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id="stockpulse_monitor_job",
        name="StockPulse Continuous Intraday Monitor",
        replace_existing=True
    )
    _SCHEDULER.start()
    print(f"[Scheduler] Started background monitoring job every {interval_seconds}s.")


def stop_scheduler():
    """Stops the background scheduler."""
    global _SCHEDULER
    if _SCHEDULER is not None and _SCHEDULER.running:
        _SCHEDULER.shutdown(wait=False)
        print("[Scheduler] Scheduler stopped.")


def is_scheduler_running() -> bool:
    """Returns True if scheduler is active."""
    return _SCHEDULER is not None and _SCHEDULER.running


def get_scheduler_status():
    """Returns runtime telemetry for the scheduler."""
    return {
        "running": is_scheduler_running(),
        "last_run": _LAST_RUN_TIME.isoformat() if _LAST_RUN_TIME else None,
        "run_count": _RUN_COUNT
    }


def trigger_immediate_evaluation():
    """Forces an immediate monitoring evaluation tick."""
    scheduled_monitoring_job()
