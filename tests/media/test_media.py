import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.models import Message
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import (
    AUDIO_DISABLED_TEXT,
    AUDIO_FAILED_TEXT,
    IMAGE_DISABLED_TEXT,
    MEDIA_TOO_LARGE_TEXT,
    ConversationService,
)
from jambachat.llm.anthropic import _to_wire as anthropic_wire
from jambachat.llm.openai_compatible import _to_wire as openai_wire
from jambachat.llm.types import ChatMessage, Image, LLMResponse
from jambachat.media.transcriber import Transcriber, TranscriptionError
from jambachat.tools.registry import ToolRegistry
from jambachat.whatsapp.client import MediaTooLargeError, WhatsAppClient, WhatsAppError
from jambachat.whatsapp.inbound import InboundMessage, parse_webhook
from tests.fakes import GRAPH_MESSAGES_URL, ScriptedLLM, graph_ok, make_settings, sent_texts

Maker = async_sessionmaker[AsyncSession]
MEDIA_INFO_URL = "https://graph.facebook.com/v23.0/777"
CDN_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=777"
STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
counter = iter(range(10_000))


def media_message(kind: str, caption: str | None = None) -> InboundMessage:
    return InboundMessage(
        wa_message_id=f"wamid.m{next(counter)}",
        phone="5511999990000",
        name="João",
        kind=kind,
        text=caption,
        timestamp=datetime.now(UTC) - timedelta(seconds=1),
        media_id="777",
        media_mime="audio/ogg; codecs=opus" if kind == "audio" else "image/jpeg",
    )


def mock_media(content: bytes = b"bytes", mime: str = "audio/ogg; codecs=opus") -> None:
    respx.get(MEDIA_INFO_URL).respond(
        json={"url": CDN_URL, "mime_type": mime, "file_size": len(content)}
    )
    respx.get(CDN_URL).respond(content=content)


async def converse(
    sessionmaker: Maker,
    llm: ScriptedLLM,
    message: InboundMessage,
    *,
    with_transcriber: bool = False,
    **settings: Any,
) -> list[str]:
    route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
    async with httpx.AsyncClient() as http:
        whatsapp = WhatsAppClient(
            http, access_token="t", phone_number_id="123", api_version="v23.0"
        )
        transcriber = (
            Transcriber(http, base_url="https://api.groq.com/openai/v1", api_key="k", model="w")
            if with_transcriber
            else None
        )
        service = ConversationService(
            sessionmaker,
            whatsapp,
            Orchestrator(llm, ToolRegistry()),
            make_settings(**settings),
            transcriber,
        )
        user_id = await service.register_inbound(message)
        assert user_id is not None
        await service.process(user_id, message)
    return sent_texts(route)


def test_parse_audio_and_image_messages() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "1",
                                    "id": "a",
                                    "timestamp": "1",
                                    "type": "audio",
                                    "audio": {"id": "111", "mime_type": "audio/ogg; codecs=opus"},
                                },
                                {
                                    "from": "1",
                                    "id": "b",
                                    "timestamp": "1",
                                    "type": "image",
                                    "image": {
                                        "id": "222",
                                        "mime_type": "image/jpeg",
                                        "caption": "nota do mercado",
                                    },
                                },
                            ]
                        }
                    }
                ]
            }
        ],
    }
    audio, image = parse_webhook(payload)

    assert (audio.kind, audio.media_id, audio.text) == ("audio", "111", None)
    assert audio.has_media
    assert (image.media_id, image.media_mime, image.text) == (
        "222",
        "image/jpeg",
        "nota do mercado",
    )


@respx.mock
async def test_download_media() -> None:
    mock_media(b"ogg-data")
    async with httpx.AsyncClient() as http:
        client = WhatsAppClient(http, access_token="t", phone_number_id="1", api_version="v23.0")
        media = await client.download_media("777", max_bytes=100)

    assert media.content == b"ogg-data"
    assert media.mime_type == "audio/ogg; codecs=opus"
    assert respx.calls.last.request.headers["authorization"] == "Bearer t"


@respx.mock
@pytest.mark.parametrize(
    ("info", "error"),
    [
        ({"url": "https://evil.example.com/x", "mime_type": "audio/ogg"}, WhatsAppError),
        ({"url": "https://fbsbx.com.evil.net/x", "mime_type": "audio/ogg"}, WhatsAppError),
        ({"url": "http://lookaside.fbsbx.com/x", "mime_type": "audio/ogg"}, WhatsAppError),
        ({"url": CDN_URL, "mime_type": "audio/ogg", "file_size": 1000}, MediaTooLargeError),
    ],
)
async def test_download_media_rejects(info: dict[str, Any], error: type[Exception]) -> None:
    respx.get(MEDIA_INFO_URL).respond(json=info)
    async with httpx.AsyncClient() as http:
        client = WhatsAppClient(http, access_token="t", phone_number_id="1", api_version="v23.0")
        with pytest.raises(error):
            await client.download_media("777", max_bytes=100)


@respx.mock
async def test_download_media_stops_when_stream_exceeds_limit() -> None:
    respx.get(MEDIA_INFO_URL).respond(json={"url": CDN_URL, "mime_type": "audio/ogg"})
    respx.get(CDN_URL).respond(content=b"x" * 500)
    async with httpx.AsyncClient() as http:
        client = WhatsAppClient(http, access_token="t", phone_number_id="1", api_version="v23.0")
        with pytest.raises(MediaTooLargeError):
            await client.download_media("777", max_bytes=100)
        with pytest.raises(WhatsAppError, match="inválido"):
            await client.download_media("../me", max_bytes=100)


@respx.mock
async def test_transcriber() -> None:
    route = respx.post(STT_URL).mock(
        side_effect=[
            httpx.Response(200, json={"text": "  gastei vinte reais  "}),
            httpx.Response(200, json={"text": ""}),
            httpx.Response(500),
        ]
    )
    async with httpx.AsyncClient() as http:
        transcriber = Transcriber(
            http, base_url="https://api.groq.com/openai/v1", api_key="k", model="whisper"
        )
        assert (
            await transcriber.transcribe(b"ogg", "audio/ogg; codecs=opus") == "gastei vinte reais"
        )
        for _ in range(2):
            with pytest.raises(TranscriptionError):
                await transcriber.transcribe(b"ogg", "audio/ogg")

    request = route.calls[0].request
    body = request.content
    assert request.headers["authorization"] == "Bearer k"
    assert b'name="model"\r\n\r\nwhisper' in body
    assert b'name="language"\r\n\r\npt' in body
    assert b'filename="audio.ogg"' in body


@respx.mock
async def test_audio_is_transcribed_and_answered(sessionmaker: Maker) -> None:
    mock_media()
    respx.post(STT_URL).respond(json={"text": "gastei 20 no almoço"})
    llm = ScriptedLLM(LLMResponse("Anotado!"))

    replies = await converse(sessionmaker, llm, media_message("audio"), with_transcriber=True)

    assert replies == ["Anotado!"]
    assert [m.content for m in llm.calls[0][1]] == ["gastei 20 no almoço"]
    async with sessionmaker() as session:
        stored = (await session.scalars(select(Message).order_by(Message.created_at))).first()
    assert stored is not None
    assert stored.content == {"text": "gastei 20 no almoço", "kind": "audio"}


@respx.mock
async def test_audio_without_transcriber_or_with_failure(sessionmaker: Maker) -> None:
    assert await converse(sessionmaker, ScriptedLLM(), media_message("audio")) == [
        AUDIO_DISABLED_TEXT
    ]
    mock_media()
    respx.post(STT_URL).respond(status_code=500)
    replies = await converse(
        sessionmaker, ScriptedLLM(), media_message("audio"), with_transcriber=True
    )
    assert replies[-1] == AUDIO_FAILED_TEXT


@respx.mock
async def test_audio_too_large(sessionmaker: Maker) -> None:
    respx.get(MEDIA_INFO_URL).respond(
        json={"url": CDN_URL, "mime_type": "audio/ogg", "file_size": 50 * 1024 * 1024}
    )
    replies = await converse(
        sessionmaker, ScriptedLLM(), media_message("audio"), with_transcriber=True
    )
    assert replies == [MEDIA_TOO_LARGE_TEXT]


@respx.mock
async def test_image_goes_to_model_when_vision_is_enabled(sessionmaker: Maker) -> None:
    mock_media(b"\xff\xd8jpeg", mime="image/jpeg")
    llm = ScriptedLLM(LLMResponse("É uma nota de R$ 87,40 do mercado."))

    replies = await converse(
        sessionmaker, llm, media_message("image", caption="registra isso"), llm_vision=True
    )

    assert replies == ["É uma nota de R$ 87,40 do mercado."]
    [user_message] = llm.calls[0][1]
    assert user_message.content == "registra isso"
    assert user_message.images == (Image("image/jpeg", b"\xff\xd8jpeg"),)


@respx.mock
async def test_image_without_vision(sessionmaker: Maker) -> None:
    replies = await converse(sessionmaker, ScriptedLLM(), media_message("image"))
    assert replies == [IMAGE_DISABLED_TEXT]


def test_images_on_the_wire() -> None:
    message = ChatMessage("user", "o que é isso?", images=(Image("image/png", b"png"),))

    openai = openai_wire(message)
    assert openai["content"][0] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,cG5n"},
    }
    assert openai["content"][1] == {"type": "text", "text": "o que é isso?"}

    [anthropic] = anthropic_wire([message])
    assert anthropic["content"][0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "cG5n",
    }
    assert json.dumps(anthropic)
