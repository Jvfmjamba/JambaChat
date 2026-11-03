import pytest

from jambachat.whatsapp.inbound import parse_webhook
from jambachat.whatsapp.signature import is_valid_signature
from tests.fakes import APP_SECRET, PHONE, sign, webhook_payload


def test_valid_signature() -> None:
    assert is_valid_signature(APP_SECRET, b"{}", sign(b"{}"))


@pytest.mark.parametrize(
    ("secret", "header"),
    [
        (APP_SECRET, None),
        (APP_SECRET, "sha1=abc"),
        (APP_SECRET, sign(b"{}", "outro")),
        (APP_SECRET, sign(b"{ }")),
        ("", sign(b"{}", "")),
    ],
)
def test_invalid_signatures(secret: str, header: str | None) -> None:
    assert not is_valid_signature(secret, b"{}", header)


def test_parses_text_message_with_contact_name() -> None:
    [message] = parse_webhook(webhook_payload("gastei 42"))

    assert message.phone == PHONE
    assert message.name == "João"
    assert message.text == "gastei 42"
    assert message.kind == "text"
    assert message.timestamp.tzinfo is not None


def test_non_text_message_has_no_text() -> None:
    [message] = parse_webhook(webhook_payload(kind="audio"))

    assert message.kind == "audio"
    assert message.text is None


def test_interactive_reply_uses_title() -> None:
    payload = webhook_payload(kind="interactive")
    raw = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    raw["interactive"] = {"type": "button_reply", "button_reply": {"id": "x", "title": "Adiar"}}

    assert parse_webhook(payload)[0].text == "Adiar"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"object": "page"},
        {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"statuses": []}}]}],
        },
        {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [{"type": "text"}]}}]}],
        },
    ],
)
def test_ignores_payloads_without_valid_messages(payload: object) -> None:
    assert parse_webhook(payload) == []


def test_location_becomes_text() -> None:
    payload = webhook_payload(kind="location")
    raw = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    raw["location"] = {"latitude": -8.05, "longitude": -34.9, "name": "Marco Zero"}
    [message] = parse_webhook(payload)

    assert (
        message.text == "[localização enviada] Marco Zero (latitude -8.05000, longitude -34.90000)"
    )


def test_broken_location_has_no_text() -> None:
    payload = webhook_payload(kind="location")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["location"] = {"latitude": "x"}
    [message] = parse_webhook(payload)

    assert message.text is None
