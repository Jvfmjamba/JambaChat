import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(tags=["health"])
logger = structlog.get_logger(__name__)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def ready(request: Request) -> dict[str, str] | JSONResponse:
    sessionmaker = request.app.state.container.sessionmaker
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("database_unavailable", error=type(exc).__name__)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
