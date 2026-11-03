import hmac
import json

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from jambachat.container import Container
from jambachat.whatsapp.inbound import parse_webhook
from jambachat.whatsapp.signature import is_valid_signature

router = APIRouter(tags=["whatsapp"])
logger = structlog.get_logger(__name__)

MAX_BODY_BYTES = 1_000_000


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413)
    return bytes(body)


@router.get("/webhook", response_class=PlainTextResponse)
async def verify(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> str:
    expected = _container(request).settings.whatsapp_verify_token.get_secret_value()
    valid = mode == "subscribe" and bool(expected) and hmac.compare_digest(token, expected)
    if not valid or not challenge.isdigit():
        raise HTTPException(status_code=403)
    return challenge


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> dict[str, str]:
    container = _container(request)
    settings = container.settings
    body = await _read_limited(request)
    signature = request.headers.get("x-hub-signature-256")
    if not is_valid_signature(settings.whatsapp_app_secret.get_secret_value(), body, signature):
        logger.warning("invalid_signature")
        raise HTTPException(status_code=401)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400) from exc

    for message in parse_webhook(payload):
        if message.phone not in settings.allowed_phones:
            logger.info("ignored_unauthorized_sender")
            continue
        user_id = await container.conversation.register_inbound(message)
        if user_id is not None:
            background.add_task(container.conversation.process, user_id, message)
    return {"status": "ok"}
