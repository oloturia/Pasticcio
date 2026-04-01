# ============================================================
# app/templates_env.py — shared Jinja2Templates instance
# ============================================================
#
# All routers that render HTML must import `templates` from here
# instead of creating their own Jinja2Templates instance.
#
# This single point of control lets us:
#   - Add global template variables (current_user, instance_name, etc.)
#   - Add custom Jinja2 filters or globals once for the whole app
#   - Avoid the subtle bug where each router has its own independent
#     template environment with no shared state
#
# Usage in a router:
#   from app.templates_env import templates
#   return templates.TemplateResponse("index.html", {"request": request, ...})

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
