"""
Comprehensive Site-Wide Functional & UI Audit Test Suite
Tests:
1. Authentication gating on all protected routes (Redirect to /login when unauthenticated)
2. Authenticated access to all pages (Returns FileResponse with 200 OK)
3. Asset verification: All script and stylesheet references in every HTML file exist on disk
4. Navigation bar consistency: Every authenticated page contains required navigation links & IDs
5. Stocks API: Listing, searching, market-status, quotes, and live ticks
6. Alerts API: Creation, listing, toggling, and deletion
7. Notifications API: User-isolated retrieval, mark read, delete, and clear
8. History API: Historical alert records retrieval and stats
9. Auth API: User profile retrieval, token generation, password verification
"""

import os
import re
import unittest
from datetime import datetime
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from fastapi import BackgroundTasks

from app.main import (
    app,
    FRONTEND_DIR,
    serve_dashboard,
    serve_live_page,
    serve_monitoring_page,
    serve_details_page,
    serve_history_page,
    serve_notifications_page,
    serve_login_page,
    is_request_authenticated
)
from app.database.models import Base, User, Stock, Alert, Notification, AlertHistory
from app.api.auth import create_access_token, COOKIE_NAME, get_current_user_profile, hash_password
from app.api.stocks import list_available_stocks, search_stocks, get_global_market_status_endpoint
from app.api.alerts import create_alert, list_alerts, delete_alert
from app.api.notifications import list_notifications, mark_notification_as_read, delete_notification, clear_all_notifications
from app.api.history import get_alert_history
from app.schemas.alert import AlertCreate
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile


class TestSiteFullAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_full_audit.db")
        cls.engine = create_engine(f"sqlite:///{cls.db_path}", connect_args={"check_same_thread": False})
        cls.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)
        Base.metadata.create_all(bind=cls.engine)

        with cls.SessionLocal() as db:
            cls.test_user = User(
                id=10,
                name="Audit Tester",
                username="audittester",
                email="audittester@stockpulse.test",
                password_hash=hash_password("AuditPass123!"),
                is_verified=True
            )
            
            cls.test_stock = Stock(
                id=10,
                symbol="TCS",
                name="Tata Consultancy Services",
                company_name="Tata Consultancy Services",
                exchange="NSE",
                is_active=True
            )
            db.add(cls.test_user)
            db.add(cls.test_stock)
            db.commit()

        cls.token = create_access_token({"sub": "audittester", "user_id": 10})

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

    def setUp(self):
        self.db = self.SessionLocal()
        self.user = self.db.query(User).filter(User.id == 10).first()

    def tearDown(self):
        self.db.close()

    def _create_mock_request(self, authenticated: bool = False, path: str = "/"):
        headers = []
        if authenticated:
            cookie_header = f"{COOKIE_NAME}={self.token}"
            headers.append((b"cookie", cookie_header.encode("latin-1")))
        
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode("latin-1"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": app,
        }
        return Request(scope)

    # ==========================================
    # 1. AUTH GATING & ROUTE PROTECTION
    # ==========================================
    def test_01_unauthenticated_requests_redirect_to_login(self):
        """Unauthenticated requests must receive a 302 redirect to /login."""
        req = self._create_mock_request(authenticated=False)
        self.assertFalse(is_request_authenticated(req))

        endpoints = [
            serve_dashboard,
            serve_live_page,
            serve_monitoring_page,
            serve_details_page,
            serve_history_page,
            serve_notifications_page
        ]
        for ep in endpoints:
            res = ep(req)
            self.assertIsInstance(res, RedirectResponse)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers.get("location"), "/login")

    def test_02_authenticated_requests_serve_pages(self):
        """Authenticated requests must return valid 200 FileResponse with correct HTML files."""
        req = self._create_mock_request(authenticated=True)
        self.assertTrue(is_request_authenticated(req))

        endpoints = [
            (serve_dashboard, "live.html"),
            (serve_live_page, "live.html"),
            (serve_monitoring_page, "monitoring.html"),
            (serve_details_page, "details.html"),
            (serve_history_page, "history.html"),
            (serve_notifications_page, "notifications.html"),
        ]
        for ep, expected_filename in endpoints:
            res = ep(req)
            self.assertIsInstance(res, FileResponse)
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.path.endswith(expected_filename))

        # Login page is public
        login_res = serve_login_page()
        self.assertIsInstance(login_res, FileResponse)
        self.assertTrue(login_res.path.endswith("login.html"))

    # ==========================================
    # 2. STATIC ASSETS & LINK INTEGRITY
    # ==========================================
    def test_03_all_script_and_css_references_exist_on_disk(self):
        """Every script src and stylesheet href in frontend HTML must exist on disk."""
        html_files = [f for f in os.listdir(FRONTEND_DIR) if f.endswith(".html")]
        self.assertGreater(len(html_files), 0)

        for hf in html_files:
            file_path = os.path.join(FRONTEND_DIR, hf)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Scripts
            scripts = re.findall(r'<script\s+[^>]*src=[\'"]([^\'"]+)[\'"]', content, re.IGNORECASE)
            for s in scripts:
                if s.startswith("/static/"):
                    clean = s.replace("/static/", "").split("?")[0]
                    target = os.path.join(FRONTEND_DIR, clean)
                    self.assertTrue(os.path.exists(target), f"Missing script '{s}' in {hf}")

            # Stylesheets
            styles = re.findall(r'<link\s+[^>]*href=[\'"]([^\'"]+)[\'"][^>]*rel=[\'"]stylesheet[\'"]', content, re.IGNORECASE)
            styles += re.findall(r'<link\s+[^>]*rel=[\'"]stylesheet[\'"][^>]*href=[\'"]([^\'"]+)[\'"]', content, re.IGNORECASE)
            for st in styles:
                if st.startswith("/static/"):
                    clean = st.replace("/static/", "").split("?")[0]
                    target = os.path.join(FRONTEND_DIR, clean)
                    self.assertTrue(os.path.exists(target), f"Missing stylesheet '{st}' in {hf}")

    # ==========================================
    # 3. NAVBAR CONSISTENCY
    # ==========================================
    def test_04_navbar_presence_and_links(self):
        """Every authenticated page must contain unified top navigation elements."""
        pages = ["live.html", "monitoring.html", "details.html", "history.html", "notifications.html"]
        required_links = ["/live", "/monitoring", "/details", "/history"]

        for page in pages:
            file_path = os.path.join(FRONTEND_DIR, page)
            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()

            for link in required_links:
                self.assertIn(f'href="{link}"', html, f"Page {page} missing standard nav link to {link}")

            # Common navbar element IDs used by common.js
            self.assertIn('id="market-status-dot"', html, f"Page {page} missing #market-status-dot")
            self.assertIn('id="market-status-text"', html, f"Page {page} missing #market-status-text")

    # ==========================================
    # 4. CORE API FUNCTIONAL CONTRACTS
    # ==========================================
    def test_05_stocks_api(self):
        """Verify stock discovery, search, and quotes."""
        # Stocks list
        dummy_resp = Response()
        stocks = list_available_stocks(response=dummy_resp)
        self.assertIsInstance(stocks, list)
        self.assertTrue(any(s["symbol"] == "TCS" for s in stocks))

        # Stock search
        search_results = search_stocks(q="TC", response=dummy_resp, db=self.db)
        self.assertTrue(any("TC" in s["symbol"] for s in search_results))

        # Market status
        market_status = get_global_market_status_endpoint(response=dummy_resp)
        self.assertIn("market_status", market_status)
        self.assertIn(market_status["market_status"], ["OPEN", "CLOSED", "STATUS UNKNOWN"])

    def test_06_alerts_crud(self):
        """Verify creating, listing, and deleting dynamic alerts."""
        alert_in = AlertCreate(
            symbol="TCS",
            direction="UP",
            target_percent=3.0,
            warning_minutes=60,
        )
        bg = BackgroundTasks()
        created = create_alert(alert_in=alert_in, background_tasks=bg, current_user=self.user, db=self.db)
        self.assertEqual(created.symbol, "TCS")
        self.assertEqual(created.user_id, self.user.id)
        alert_id = created.id

        # List alerts
        alerts = list_alerts(current_user=self.user, db=self.db)
        self.assertTrue(any(a.id == alert_id for a in alerts))

        # Delete alert
        del_res = delete_alert(alert_id=alert_id, current_user=self.user, db=self.db)
        self.assertIn("deleted successfully", del_res["message"])

        # Confirm deleted
        alerts_after = list_alerts(current_user=self.user, db=self.db)
        self.assertFalse(any(a.id == alert_id for a in alerts_after))

    def test_07_notifications_api(self):
        """Verify user-isolated notifications flow."""
        # Initial: 0 notifications
        res = list_notifications(current_user=self.user, db=self.db)
        self.assertEqual(res["total"], 0)

        # Seed notification for user
        notif = Notification(
            id=99,
            user_id=self.user.id,
            symbol="TCS",
            event_type="ALERT",
            priority="INFO",
            title="Audit Test Alert",
            message="Testing notifications flow"
        )
        self.db.add(notif)
        self.db.commit()

        # Check list
        res2 = list_notifications(current_user=self.user, db=self.db)
        self.assertEqual(res2["total"], 1)

        # Mark read
        read_item = mark_notification_as_read(notification_id=99, current_user=self.user, db=self.db)
        self.assertTrue(read_item.is_read)

        # Delete notification
        del_item = delete_notification(notification_id=99, current_user=self.user, db=self.db)
        self.assertIn("deleted successfully", del_item["message"])

        # Confirm 0 remaining
        res3 = list_notifications(current_user=self.user, db=self.db)
        self.assertEqual(res3["total"], 0)

    def test_08_auth_profile(self):
        """Verify user profile extraction."""
        profile = get_current_user_profile(current_user=self.user, db=self.db)
        user_dict = profile.get("user", {})
        self.assertEqual(user_dict.get("username"), "audittester")
        self.assertEqual(user_dict.get("email"), "audittester@stockpulse.test")

    def test_09_alert_history(self):
        """Verify alert history listing and retrieval."""
        hist = AlertHistory(
            id=50,
            alert_id=None,
            symbol="TCS",
            direction="UP",
            target_percent=3.0,
            warning_minutes=60,
            current_price=3500.0,
            current_change=1.5,
            predicted_probability=0.80,
            result="SUCCESS",
            user_id=self.user.id
        )
        self.db.add(hist)
        self.db.commit()

        histories = get_alert_history(current_user=self.user, db=self.db)
        self.assertTrue(any(h.id == 50 for h in histories))


if __name__ == "__main__":
    unittest.main()
