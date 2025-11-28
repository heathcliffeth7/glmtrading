"""CORS Middleware Configuration"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import get_dashboard_settings


def setup_cors(app: FastAPI) -> None:
    """Configure CORS middleware for the dashboard API"""
    settings = get_dashboard_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count", "X-Page", "X-Per-Page"],
    )
