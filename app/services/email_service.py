import os
import smtplib
import json
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple, Optional


def _send_via_resend(api_key: str, from_name: str, to_email: str, recipient_name: str, otp_code: str, html_content: str, plain_text: str) -> Tuple[bool, Optional[str]]:
    """Dispatches verification email via Resend REST API (HTTPS Port 443). Works unconditionally on Render."""
    try:
        from_email = os.getenv("RESEND_FROM", "StockPulse Security <onboarding@resend.dev>").strip()
        payload = {
            "from": from_email,
            "to": [to_email],
            "subject": f"{otp_code} is your StockPulse verification code",
            "html": html_content,
            "text": plain_text
        }
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "User-Agent": "StockPulse/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status in (200, 201):
                print(f"[EmailService] Resend API: OTP email successfully delivered to {to_email}")
                return True, None
            return False, f"Resend API status {resp.status}"
    except Exception as e:
        return False, f"Resend API error: {e}"


def _send_via_brevo_api(api_key: str, from_email: str, from_name: str, to_email: str, recipient_name: str, otp_code: str, html_content: str, plain_text: str) -> Tuple[bool, Optional[str]]:
    """Dispatches verification email via Brevo REST API (HTTPS Port 443). Works unconditionally on Render."""
    try:
        payload = {
            "sender": {"name": from_name, "email": from_email},
            "to": [{"email": to_email, "name": recipient_name}],
            "subject": f"{otp_code} is your StockPulse verification code",
            "htmlContent": html_content,
            "textContent": plain_text
        }
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "api-key": api_key.strip(),
                "Content-Type": "application/json",
                "User-Agent": "StockPulse/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status in (200, 201):
                print(f"[EmailService] Brevo API: OTP email successfully delivered to {to_email}")
                return True, None
            return False, f"Brevo API status {resp.status}"
    except Exception as e:
        return False, f"Brevo API error: {e}"


def send_otp_email(to_email: str, recipient_name: str, otp_code: str) -> Tuple[bool, Optional[str], bool]:
    """
    Sends a 6-digit OTP email for registration verification.
    Delivery methods:
      1. Resend HTTP API (Port 443 - bypasses cloud SMTP port blocks on Render/Vercel)
      2. Brevo HTTP API (Port 443 - bypasses cloud SMTP port blocks on Render/Vercel)
      3. Standard SMTP (Gmail App Password, Hostinger, AWS SES, etc. - requires unblocked port 587)

    Returns:
        (success: bool, error_message: Optional[str], service_configured: bool)
    """
    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    brevo_key = os.getenv("BREVO_API_KEY", "").strip()

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
    from_email = os.getenv("SMTP_FROM_EMAIL", "").strip() or smtp_user or "verify@stockpulse.io"
    from_name = os.getenv("SMTP_FROM_NAME", "StockPulse Security").strip()

    # Detect if Brevo API key was placed in SMTP_PASSWORD
    if not brevo_key and smtp_pass.startswith("xkeysib-"):
        brevo_key = smtp_pass

    # Check system_settings DB table if env vars are empty
    if not resend_key and not brevo_key and (not smtp_user or not smtp_pass):
        try:
            from app.database.database import SessionLocal
            from app.database.models import SystemSetting
            db = SessionLocal()
            try:
                row_resend = db.query(SystemSetting).filter(SystemSetting.key == "resend_api_key").first()
                if row_resend and row_resend.value:
                    resend_key = row_resend.value

                row_user = db.query(SystemSetting).filter(SystemSetting.key == "smtp_username").first()
                row_pass = db.query(SystemSetting).filter(SystemSetting.key == "smtp_password").first()
                row_host = db.query(SystemSetting).filter(SystemSetting.key == "smtp_host").first()
                row_port = db.query(SystemSetting).filter(SystemSetting.key == "smtp_port").first()
                if row_user and row_user.value and row_pass and row_pass.value:
                    smtp_user = row_user.value
                    smtp_pass = row_pass.value.replace(" ", "").strip()
                    if row_host and row_host.value:
                        smtp_host = row_host.value.strip()
                    if row_port and row_port.value:
                        smtp_port = int(row_port.value.strip())
                    from_email = smtp_user
            finally:
                db.close()
        except Exception:
            pass

    service_configured = bool(resend_key or brevo_key or (smtp_user and smtp_pass))

    # If neither API nor SMTP is configured
    if not service_configured:
        print("\n" + "=" * 65)
        print(f"[StockPulse OTP Alert] Verification OTP for {to_email} ({recipient_name}):")
        print(f"       >>> CODE: {otp_code} <<<")
        print(" [Notice: To deliver to real inboxes, configure RESEND_API_KEY or SMTP credentials]")
        print("=" * 65 + "\n")
        return False, "NO_CREDENTIALS", False

    plain_text = f"""Hello {recipient_name},

Thank you for choosing StockPulse.

Your 6-digit verification code is:
{otp_code}

This code will expire in 10 minutes. Please enter it in the StockPulse verification window to activate your account.

If you did not request this code, you can safely ignore this email.

Best regards,
The StockPulse Security Team
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>StockPulse Verification</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; padding: 40px 15px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 520px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.08); overflow: hidden;" cellspacing="0" cellpadding="0">
          
          <!-- Header Banner -->
          <tr>
            <td style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 32px 30px; text-align: center;">
              <div style="display: inline-block; width: 44px; height: 44px; line-height: 44px; background: rgba(255, 255, 255, 0.2); border-radius: 12px; margin-bottom: 12px; font-size: 22px; color: #ffffff;">
                ⚡
              </div>
              <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 800; letter-spacing: -0.5px;">StockPulse</h1>
              <p style="margin: 4px 0 0 0; color: rgba(255, 255, 255, 0.85); font-size: 13px;">Real-Time Market Predictions & Alerts</p>
            </td>
          </tr>

          <!-- Main Content -->
          <tr>
            <td style="padding: 36px 32px 24px 32px;">
              <h2 style="margin: 0 0 12px 0; color: #0f172a; font-size: 19px; font-weight: 700;">Verify Your Email Address</h2>
              <p style="margin: 0 0 24px 0; color: #475569; font-size: 14px; line-height: 1.6;">
                Hi <strong>{recipient_name}</strong>, thanks for creating an account. Please use the following 6-digit verification code to complete your registration:
              </p>

              <!-- OTP Code Display Card -->
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 24px;">
                <tr>
                  <td align="center" style="background-color: #f1f5f9; border-radius: 12px; border: 1px dashed #cbd5e1; padding: 22px 16px;">
                    <div style="font-family: 'Courier New', Courier, monospace; font-size: 36px; font-weight: 800; letter-spacing: 8px; color: #2563eb; text-align: center;">
                      {otp_code}
                    </div>
                    <div style="margin-top: 8px; font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px;">
                      ⏱️ Valid for 10 minutes
                    </div>
                  </td>
                </tr>
              </table>

              <p style="margin: 0 0 8px 0; color: #64748b; font-size: 13px; line-height: 1.5;">
                • Never share this verification code with anyone.<br>
                • StockPulse support will never ask you for this code.<br>
                • If you did not create this account, you can safely ignore this email.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #f8fafc; border-top: 1px solid #f1f5f9; padding: 20px 32px; text-align: center;">
              <p style="margin: 0; color: #94a3b8; font-size: 12px;">
                © 2026 StockPulse. Real-Time Algorithmic Intelligence.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    # 1. Primary Cloud Delivery: Resend REST API (HTTPS Port 443 - Never blocked on Render Free)
    if resend_key:
        ok, err = _send_via_resend(resend_key, from_name, to_email, recipient_name, otp_code, html_content, plain_text)
        if ok:
            return True, None, True
        print(f"[EmailService] Resend attempt failed: {err}")

    # 2. Cloud Delivery Alternative: Brevo REST API (HTTPS Port 443 - Never blocked on Render Free)
    if brevo_key:
        ok, err = _send_via_brevo_api(brevo_key, from_email, from_name, to_email, recipient_name, otp_code, html_content, plain_text)
        if ok:
            return True, None, True
        print(f"[EmailService] Brevo attempt failed: {err}")

    # 3. Standard SMTP (Port 587)
    if smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"{otp_code} is your StockPulse verification code"
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = to_email

            msg.attach(MIMEText(plain_text, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=8) as server:
                server.ehlo()
                if os.getenv("SMTP_TLS", "true").lower() in ("true", "1", "yes"):
                    server.starttls()
                    server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_email, [to_email], msg.as_string())

            print(f"[EmailService] SMTP: OTP successfully sent to {to_email}")
            return True, None, True

        except Exception as e:
            err_msg = str(e)
            print(f"[EmailService] SMTP delivery failed to {to_email}: {err_msg}")
            print(f"[EmailService] LOGGED OTP for {to_email}: {otp_code}")

            is_cloud_blocked = any(k in err_msg.lower() for k in ("101", "unreachable", "timed out", "connection refused"))
            if is_cloud_blocked:
                return False, "CLOUD_FIREWALL_BLOCKED_SMTP", True
            return False, f"SMTP_ERROR: {err_msg}", True

    return False, "DELIVERY_FAILED", True
