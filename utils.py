import os
import random
import hashlib

try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE", "")
DEMO_MODE = not (TWILIO_AVAILABLE and TWILIO_SID and TWILIO_TOKEN and TWILIO_PHONE)


def hp(password):
    return hashlib.sha256(password.encode()).hexdigest()


def fmt(qty):
    return str(int(qty)) if qty == int(qty) else f"{qty:.2f}"


def send_otp(mobile, app=None):
    from database import store_otp
    otp = str(random.randint(100000, 999999))
    store_otp(mobile, otp)
    if DEMO_MODE:
        if app is not None:
            app.demo_otp = otp
        print(f"[DEMO OTP] {mobile} -> {otp}")
    else:
        try:
            TwilioClient(TWILIO_SID, TWILIO_TOKEN).messages.create(
                body=f"Aurora Grocery OTP: {otp} (valid 10 min)",
                from_=TWILIO_PHONE,
                to=mobile,
            )
        except Exception as e:
            print(f"Twilio error: {e}")
    return otp
