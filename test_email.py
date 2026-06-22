from backend.app.config import settings
import smtplib

print("Testing SMTP connection...")
print(f"  Host:     {settings.smtp_host}")
print(f"  Port:     {settings.smtp_port}")
print(f"  User:     {settings.smtp_user}")
print(f"  Password: {'*' * len(settings.smtp_password)}")
print(f"  Email on: {settings.enable_email}")
print()

try:
    print("Step 1: Connecting to smtp.gmail.com:587 ...")
    smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
    print("Step 2: EHLO ...")
    smtp.ehlo()
    print("Step 3: STARTTLS ...")
    smtp.starttls()
    smtp.ehlo()
    print("Step 4: Logging in ...")
    smtp.login(settings.smtp_user, settings.smtp_password)
    print()
    print("SUCCESS: SMTP login worked, emails should send correctly.")
    smtp.quit()
except smtplib.SMTPAuthenticationError as e:
    print()
    print("AUTH ERROR: Gmail rejected the password.")
    print("Detail:", str(e))
    print()
    print("Fix: You need a Gmail App Password (not your normal Gmail password).")
    print("1. Go to: https://myaccount.google.com/apppasswords")
    print("2. Enable 2-Step Verification if not already on")
    print("3. Generate an App Password for Mail")
    print("4. Paste the 16-character code as SMTP_PASSWORD in your .env file")
except smtplib.SMTPConnectError as e:
    print()
    print("CONNECT ERROR: Could not reach Gmail SMTP server.")
    print("Detail:", str(e))
except TimeoutError:
    print()
    print("TIMEOUT: Could not connect to smtp.gmail.com:587 within 10 seconds.")
    print("Your firewall or antivirus may be blocking outbound port 587.")
    print("Try disabling Windows Defender Firewall temporarily and run again.")
except Exception as e:
    print()
    print(f"ERROR ({type(e).__name__}): {e}")
