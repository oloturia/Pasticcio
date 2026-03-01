# ============================================================
# app/main.py — application entry point
# ============================================================
#
# This is the file uvicorn loads at startup.
# For now it contains just the base app with a health check endpoint.
# As we develop, we'll add routers for recipes, users,
# ActivityPub, etc.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth, recipes

# Create the main FastAPI instance.
# The metadata appears in the auto-generated documentation at /api/docs
app = FastAPI(
    title="Pasticcio",
    description="A federated, open-source recipe social network",
    version="0.1.0",
    # In production we might want to disable public docs
    docs_url="/api/docs" if settings.debug else None,
    redoc_url="/api/redoc" if settings.debug else None,
)

# --- CORS (Cross-Origin Resource Sharing) ---
# Allows the frontend (even on a different domain) to make API requests.
# In development we're permissive; in production only authorised
# domains should be listed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [f"https://{settings.instance_domain}"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health check ---
# Simple endpoint to verify the app is responding.
# Useful for Podman/Docker healthchecks and load balancers.
@app.get("/health", tags=["system"])
async def health_check():
    return {
        "status": "ok",
        "instance": settings.instance_name,
        "version": "0.1.0",
    }


# --- Routers ---
app.include_router(auth.router)
app.include_router(recipes.router)


# --- Root endpoint ---
@app.get("/", tags=["system"])
async def root():
    return {
        "name": settings.instance_name,
        "description": settings.instance_description,
        "software": "pasticcio",
        "version": "0.1.0",
        "source_code": "https://github.com/TBD/pasticcio",
    }
