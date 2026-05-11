# ============================================================
# app/routers/frontend_auth.py — HTML login, register, verify pages
# ============================================================
#
# Routes:
#   GET  /login          → render login form
#   POST /login          → validate credentials, set cookie, redirect to /
#   GET  /register       → render registration form
#   POST /register       → create account (inactive), send confirm email
#   GET  /verify         → activate account via token, redirect to /login
#   GET  /verify-pending → "check your email" confirmation page
#   POST /logout         → clear cookie, redirect to /
#
# The JWT is stored in an httponly cookie called "session".
# httponly prevents JavaScript from reading it (XSS protection).
# The REST API still uses Authorization headers for non-browser clients.

import uuid

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ap.signatures import generate_rsa_keypair
from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.email import consume_verification_token, create_verification_token, send_confirmation_email
from app.models.user import User
from app.templates_env import templates

router = APIRouter(tags=["frontend"])

SESSION_COOKIE = "session"
COOKIE_MAX_AGE = settings.jwt_expire_minutes * 60


# ============================================================
# Login
# ============================================================

@router.get("/login")
async def login_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Render the login form. Redirect to / if already logged in."""
    if current_user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "confirmed": request.query_params.get("confirmed") == "1",
        "username": None,
        "current_user": None,
    })


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Validate credentials and set a session cookie.

    On success: set httponly JWT cookie and redirect to /.
    On failure: re-render form with a generic error message.
    We never reveal whether the username or password was wrong
    specifically — this prevents username enumeration.
    """
    result = await db.execute(
        select(User).where(User.username == username.lower().strip())
    )
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Incorrect username or password.",
            "confirmed": False,
            "username": username,
            "current_user": None,
        }, status_code=401)

    if not user.is_active:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Account not active. Please check your email to confirm your account.",
            "confirmed": False,
            "username": username,
            "current_user": None,
        }, status_code=403)

    token = create_access_token(str(user.id))
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
    )
    return response


# ============================================================
# Register
# ============================================================

@router.get("/register")
async def register_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Render the registration form."""
    if not settings.enable_registrations:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Registrations are currently closed on this instance.",
            "confirmed": False,
            "username": None,
            "current_user": None,
        })
    if current_user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": None,
        "username": None,
        "email": None,
        "display_name": None,
        "current_user": None,
    })


@router.post("/register")
async def register_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new account and send a confirmation email.

    The account is created with is_active=False. The user must click
    the link in the confirmation email to activate it.
    On success: redirect to /verify-pending.
    On failure: re-render the form with an error and pre-filled fields.
    """
    if not settings.enable_registrations:
        return RedirectResponse("/login", status_code=302)

    username = username.lower().strip()
    display_name = display_name.strip() or None

    error = None
    if len(username) < 3:
        error = "Username must be at least 3 characters."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif not username.replace("_", "").replace("-", "").isalnum():
        error = "Username may only contain letters, numbers, hyphens and underscores."

    if error:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": error,
            "username": username,
            "email": email,
            "display_name": display_name,
            "current_user": None,
        }, status_code=422)

    existing = await db.execute(
        select(User).where(
            (User.username == username) | (User.email == email)
        )
    )
    if existing.scalar_one_or_none():
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Username or email already in use.",
            "username": username,
            "email": email,
            "display_name": display_name,
            "current_user": None,
        }, status_code=409)

    private_key_pem, public_key_pem = generate_rsa_keypair()
    user_id = uuid.uuid4()
    ap_id = f"https://{settings.instance_domain}/users/{username}"

    user = User(
        id=user_id,
        username=username,
        email=email,
        display_name=display_name or username,
        hashed_password=hash_password(password),
        ap_id=ap_id,
        is_remote=False,
        public_key=public_key_pem,
        private_key=private_key_pem,
        is_active=False,
    )
    db.add(user)
    await db.flush()

    token = await create_verification_token(user_id)
    await send_confirmation_email(email=email, username=username, token=token)

    return RedirectResponse("/verify-pending", status_code=302)


# ============================================================
# Email verification
# ============================================================

@router.get("/verify-pending")
async def verify_pending_page(request: Request):
    """Render the 'check your email' page shown after registration."""
    return templates.TemplateResponse("verify_pending.html", {
        "request": request,
        "current_user": None,
    })


@router.get("/verify")
async def verify_email(
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Activate a user account via the token received by email.

    If valid: sets is_active=True and redirects to /login?confirmed=1.
    If invalid or expired: renders the login page with an error.
    """
    user_id = await consume_verification_token(token)

    if user_id is None:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "This confirmation link is invalid or has expired. Please register again.",
            "confirmed": False,
            "username": None,
            "current_user": None,
        }, status_code=400)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Account not found. Please register again.",
            "confirmed": False,
            "username": None,
            "current_user": None,
        }, status_code=404)

    if user.is_active:
        return RedirectResponse("/login", status_code=302)

    user.is_active = True
    await db.flush()

    return RedirectResponse("/login?confirmed=1", status_code=302)


# ============================================================
# Logout
# ============================================================

@router.post("/logout")
async def logout():
    """Clear the session cookie and redirect to /."""
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
