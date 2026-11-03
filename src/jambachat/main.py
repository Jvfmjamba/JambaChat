from fastapi import FastAPI

from jambachat import __version__
from jambachat.api import health
from jambachat.config import get_settings
from jambachat.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.is_production)

    app = FastAPI(
        title="JambaChat",
        version=__version__,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.include_router(health.router)
    return app


app = create_app()
