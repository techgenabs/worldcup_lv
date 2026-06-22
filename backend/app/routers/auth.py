import random
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from ..database import get_db, row
from ..schemas import LoginRequest, RegisterRequest, TokenResponse
from ..security import create_token, hash_password, verify_password
from ..services.audit import audit
from ..services.emailer import queue_notification
from ..services.settings import get_registration_requirements
router = APIRouter(prefix="/auth", tags=["auth"])
@router.get("/registration-settings")
def registration_settings():
    with get_db() as db:
        return get_registration_requirements(db)
@router.post("/register")
def register(payload: RegisterRequest):
    otp = str(random.randint(100000, 999999))
    with get_db() as db:
        requirements = get_registration_requirements(db)
        if requirements["email_required"] and not payload.email:
            raise HTTPException(status_code=422, detail="Email is required for registration")
        if requirements["mobile_required"] and not payload.mobile:
            raise HTTPException(status_code=422, detail="Mobile number is required for registration")
        email = str(payload.email).strip() if payload.email else f"guest-{uuid4().hex}@worldcup.local"
        mobile = payload.mobile.strip() if payload.mobile else None
        duplicate_checks = []
        params = []
        if payload.email:
            duplicate_checks.append("email = ?")
            params.append(email)
        if mobile:
            duplicate_checks.append("mobile = ?")
            params.append(mobile)
        existing = row(db.execute(f"SELECT id FROM users WHERE {' OR '.join(duplicate_checks)}", tuple(params))) if duplicate_checks else None
        if existing:
            raise HTTPException(status_code=409, detail="Email or mobile already registered")
        cur = db.execute(
            """
            INSERT INTO users (name, email, mobile, country, password_hash, otp_code, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (payload.name, email, mobile, payload.country, hash_password(payload.password), otp),
        )
        user = row(db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)))
        if payload.email:
            queue_notification(db, email, "Welcome to WorldCup 2026", f"Your OTP code is {otp}", user["id"])

        # ── Notify admin of every new registration, with the user's details ──
        admin_body = (
            f"A new user has registered and is pending activation.\n\n"
            f"Name: {payload.name}\n"
            f"Email: {email}\n"
            f"Mobile: {mobile or 'Not provided'}\n"
            f"Country: {payload.country}\n"
            f"User ID: {user['id']}\n"
            f"Registered at: {user.get('created_at', '')}\n\n"
            f"Log in to the admin panel to activate this account."
        )
        queue_notification(
            db,
            "singhamarpkr@gmail.com",
            f"New WorldCup 2026 user registered: {payload.name}",
            admin_body,
            user["id"],
        )

        audit(db, "register", "user", user["id"], user["id"], {"email": email, "mobile": mobile, "requirements": requirements})
    # New accounts are inactive by default (is_active = 0) until an admin
    # activates them — no access token is issued at registration time.
    return {
        "message": "Account created successfully. Your account is pending activation — "
                    "please contact the admin at singhamarpkr@gmail.com to get activated.",
        "user_id": user["id"],
    }
@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    with get_db() as db:
        user = row(
            db.execute(
                "SELECT * FROM users WHERE email = ? OR mobile = ?",
                (payload.identifier, payload.identifier),
            )
        )
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user["is_active"]:
        raise HTTPException(
            status_code=403,
            detail="Your account is pending activation. Please contact the admin at singhamarpkr@gmail.com to activate your account.",
        )
    with get_db() as db:
        db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
        audit(db, "login", "user", user["id"], user["id"], {"identifier": payload.identifier})
    return TokenResponse(access_token=create_token(str(user["id"]), user["role"]), user=user)
@router.post("/verify-otp")
def verify_otp(identifier: str, otp_code: str):
    with get_db() as db:
        user = row(db.execute("SELECT * FROM users WHERE email = ? OR mobile = ?", (identifier, identifier)))
        if not user or user["otp_code"] != otp_code:
            raise HTTPException(status_code=400, detail="Invalid OTP")
        db.execute("UPDATE users SET otp_verified = 1, otp_code = NULL WHERE id = ?", (user["id"],))
        audit(db, "verify_otp", "user", user["id"], user["id"])
    return {"status": "verified"}
