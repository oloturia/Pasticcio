# ============================================================
# app/routers/static_pages.py — static informational pages
# ============================================================
#
# Routes:
#   GET /about   → about page (project philosophy, instance info)
#   GET /contact → contact page (administrator email, bug reports)
#   GET /terms   → terms of service and privacy policy
#
# All pages read instance settings from config so the administrator
# can customise them via environment variables without touching templates.

from fastapi import APIRouter, Depends, Request

from app.config import settings
from app.dependencies import get_current_user_optional
from app.models.user import User
from app.templates_env import templates

router = APIRouter(tags=["frontend"])


def _base_ctx(request: Request, current_user) -> dict:
    """Common context variables shared by all static pages."""
    return {
        "request": request,
        "current_user": current_user,
        "instance_name": settings.instance_name,
        "instance_description": settings.instance_description,
        "instance_domain": settings.instance_domain,
        "instance_contact": settings.instance_contact,
    }


@router.get("/about")
async def about_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Render the About page."""
    return templates.TemplateResponse("about.html", _base_ctx(request, current_user))


@router.get("/contact")
async def contact_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Render the Contact page."""
    return templates.TemplateResponse("contact.html", _base_ctx(request, current_user))


@router.get("/terms")
async def terms_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Render the Terms & Privacy page."""
    ctx = _base_ctx(request, current_user)
    # Express JWT expiry in hours for the cookie notice
    ctx["jwt_expire_hours"] = settings.jwt_expire_minutes // 60
    return templates.TemplateResponse("terms.html", ctx)
