# ============================================================
# app/main.py — application entry point
# ============================================================
#
# This is the file uvicorn loads at startup.
# As we develop, we'll add routers for recipes, users,
# ActivityPub, etc.

import app.models  # noqa: F401 — registers all ORM models in Base.metadata

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth, recipes, wellknown, activitypub

# Create the main FastAPI instance.
# The metadata appears in the auto-generated documentation at /api/docs
app = FastAPI(
    title="Pasticcio",
    description="A federated, open-source recipe social network",
    version="0.1.0",
    docs_url="/api/docs" if settings.debug else None,
    redoc_url="/api/redoc" if settings.debug else None,
)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [f"https://{settings.instance_domain}"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health check ---
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
app.include_router(wellknown.router)    # /.well-known/webfinger, /nodeinfo
app.include_router(activitypub.router)  # /users/{username}, /users/{username}/inbox, /outbox


# --- Root ---
@app.get("/", tags=["system"])
async def root():
    return {
        "name": settings.instance_name,
        "description": settings.instance_description,
        "software": "pasticcio",
        "version": "0.1.0",
        "source_code": "https://github.com/TBD/pasticcio",
    }
