import json

from fastapi import APIRouter, HTTPException, Request

from jambachat.container import Container
from jambachat.wallet.service import WalletAuthError, WalletInvalidError, WalletRateLimitError

router = APIRouter(tags=["integracoes"])

MAX_BODY_BYTES = 16_384


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


@router.post("/integrations/wallet")
async def record_purchase(request: Request) -> dict[str, str]:
    container: Container = request.app.state.container
    if container.wallet is None:
        raise HTTPException(status_code=404)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401)
    body = await _read_limited(request)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400)
    try:
        message = await container.wallet.record(token.strip(), payload)
    except WalletAuthError as exc:
        raise HTTPException(status_code=401) from exc
    except WalletRateLimitError as exc:
        raise HTTPException(status_code=429) from exc
    except WalletInvalidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "message": message}
