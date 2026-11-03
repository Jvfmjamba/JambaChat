from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from jambachat import __version__
from jambachat.api import health, webhook
from jambachat.config import get_settings
from jambachat.container import Container, build_container
from jambachat.logging import configure_logging


def create_app(container: Container | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.is_production)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        app.state.container = container or build_container(settings)
        if owned:
            app.state.container.start()
        try:
            yield
        finally:
            if owned:
                await app.state.container.aclose()

    app = FastAPI(
        title="JambaChat",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    if container is not None:
        app.state.container = container
    app.include_router(health.router)
    app.include_router(webhook.router)
    return app


app = create_app()
