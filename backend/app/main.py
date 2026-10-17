from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, debug=settings.debug, version="0.1.0")
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
