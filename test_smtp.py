"""
StockPulse SMTP Tester Utility
Run this script to verify your SMTP connection and credentials:
    python test_smtp.py your_email@gmail.com
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

from app.services.email_service import send_otp_email

def main():
    recipient = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMTP_USER")

    if not recipient:
        print("[!] Usage: python test_smtp.py <your_email@example.com>")
        print("[!] Or set SMTP_USER in your .env file first.")
        return

    print("=" * 60)
    print("Testing StockPulse SMTP Configuration...")
    print(f"SMTP_HOST:       {os.getenv('SMTP_HOST', 'smtp.gmail.com')}")
    print(f"SMTP_PORT:       {os.getenv('SMTP_PORT', '587')}")
    print(f"SMTP_USER:       {os.getenv('SMTP_USER', '(empty)')}")
    print(f"SMTP_FROM_EMAIL: {os.getenv('SMTP_FROM_EMAIL', os.getenv('SMTP_USER', '(empty)'))}")
    print(f"Sending test OTP email to: {recipient}")
    print("=" * 60)

    success, error_msg, smtp_configured = send_otp_email(
        to_email=recipient,
        recipient_name="StockPulse Tester",
        otp_code="884422"
    )

    if success:
        print("\n[SUCCESS] Email sent successfully! Please check your inbox (and Spam folder).")
    elif not smtp_configured:
        print("\n[FAILED] SMTP_USER or SMTP_PASSWORD is not set in .env.")
    else:
        print(f"\n[ERROR] Delivery failed: {error_msg}")
        if "Username and Password not accepted" in str(error_msg):
            print("\nHint for Gmail: You must use a 16-character 'Google App Password', not your regular Gmail account password.")
            print("Generate one here: https://myaccount.google.com/apppasswords")

if __name__ == "__main__":
    main()
