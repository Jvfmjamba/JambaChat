import json

import httpx
import pytest
import respx

from jambachat.whatsapp.client import WhatsAppClient, WhatsAppError, document_type, split_text
from tests.fakes import GRAPH_MESSAGES_URL, graph_ok


def make_client(http: httpx.AsyncClient) -> WhatsAppClient:
    return WhatsAppClient(http, access_token="tk", phone_number_id="123", api_version="v23.0")


def test_split_text_respects_limit_and_prefers_line_breaks() -> None:
    text = ("a" * 30 + "\n") * 10
    chunks = split_text(text, limit=100)

    assert all(len(c) <= 100 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == "a" * 300
    assert split_text("curto") == ["curto"]
    assert split_text("   ") == []
    assert split_text("x" * 250, limit=100) == ["x" * 100, "x" * 100, "x" * 50]


@respx.mock
async def test_send_text_template_and_mark_read() -> None:
    route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
    async with httpx.AsyncClient() as http:
        client = make_client(http)
        ids = await client.send_text("5511", "oi")
        template_id = await client.send_template("5511", "lembrete", ["pagar luz"])
        await client.mark_read("wamid.in")

    bodies = [json.loads(c.request.content) for c in route.calls]
    assert ids == ["wamid.out"]
    assert template_id == "wamid.out"
    assert route.calls[0].request.headers["authorization"] == "Bearer tk"
    assert bodies[0]["text"] == {"body": "oi", "preview_url": False}
    assert bodies[1]["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "pagar luz"}
    ]
    assert bodies[2] == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.in",
    }


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {}}),
        httpx.Response(200, json={}),
        httpx.Response(200, text="x"),
    ],
)
async def test_errors_raise_whatsapp_error(response: httpx.Response) -> None:
    respx.post(GRAPH_MESSAGES_URL).mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(WhatsAppError):
            await make_client(http).send_text("5511", "oi")


def test_document_type_falls_back_to_plain_text() -> None:
    assert document_type("application/pdf") == "application/pdf"
    assert document_type("text/csv") == "text/plain"
    assert document_type("application/json") == "text/plain"
