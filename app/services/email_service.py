import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple, Optional


def send_otp_email(to_email: str, recipient_name: str, otp_code: str) -> Tuple[bool, Optional[str]]:
    """
    Sends a 6-digit OTP email for registration verification via SMTP.
    Supports standard SMTP (Gmail App Password, Brevo, SendGrid, Amazon SES, etc.).
    If SMTP credentials are not configured, prints the OTP in server logs for testing.

    Returns:
        (success: bool, error_message: Optional[str])
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", smtp_user or "verify@stockpulse.io")
    from_name = os.getenv("SMTP_FROM_NAME", "StockPulse Security")

    # If SMTP is not yet configured in .env, display a prominent log for easy testing
    if not smtp_user or not smtp_pass:
        print("\n" + "=" * 65)
        print(f"[StockPulse OTP Alert] Verification OTP for {to_email} ({recipient_name}):")
        print(f"       >>> CODE: {otp_code} <<<")
        print(" [Notice: To deliver to real inboxes, add SMTP_USER & SMTP_PASSWORD to .env]")
        print("=" * 65 + "\n")
        return True, None

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{otp_code} is your StockPulse verification code"
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email

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

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Send via SMTP
        with smtplib.SMTP(smtp_host, smtp_port, timeout=12) as server:
            server.ehlo()
            if os.getenv("SMTP_TLS", "true").lower() in ("true", "1", "yes"):
                server.starttls()
                server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [to_email], msg.as_string())

        print(f"[EmailService] OTP successfully sent to {to_email}")
        return True, None

    except Exception as e:
        err_msg = str(e)
        print(f"[EmailService] Failed to send email to {to_email}: {err_msg}")
        # Even if SMTP connection throws error, print code to console so developer is never locked out
        print(f"[EmailService] FALLBACK OTP for {to_email}: {otp_code}")
        return False, f"Email delivery error: {err_msg}"
