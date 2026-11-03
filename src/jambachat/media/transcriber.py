import httpx
import structlog

from jambachat.llm.factory import ensure_secure_endpoint

logger = structlog.get_logger(__name__)

EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/amr": "amr",
    "audio/wav": "wav",
    "audio/webm": "webm",
}


class TranscriptionError(Exception):
    pass


class Transcriber:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "pt",
        timeout_seconds: float = 60.0,
    ) -> None:
        ensure_secure_endpoint(base_url)
        self._http = http
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._model = model
        self._language = language
        self._timeout = timeout_seconds

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        base_mime = mime_type.split(";")[0].strip().lower()
        filename = f"audio.{EXTENSIONS.get(base_mime, 'ogg')}"
        try:
            response = await self._http.post(
                self._url,
                headers=self._headers,
                data={"model": self._model, "language": self._language, "response_format": "json"},
                files={"file": (filename, audio, base_mime)},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"falha de rede: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise TranscriptionError(f"transcrição respondeu {response.status_code}")
        try:
            text = response.json().get("text")
        except (ValueError, AttributeError) as exc:
            raise TranscriptionError("resposta de transcrição inesperada") from exc
        if not isinstance(text, str) or not text.strip():
            raise TranscriptionError("áudio sem fala reconhecível")
        return text.strip()
