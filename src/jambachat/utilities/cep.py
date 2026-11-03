import re
from typing import Any

import httpx

from jambachat.tools.http import TTLCache, request_json
from jambachat.tools.registry import ToolError

VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"


def normalize_cep(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise ToolError("CEP inválido; ele tem 8 dígitos, ex.: 01001-000")
    return digits


class CepClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        self._cache = TTLCache(24 * 3600)

    async def lookup(self, cep: str) -> dict[str, Any]:
        digits = normalize_cep(cep)
        cached = self._cache.get(digits)
        if isinstance(cached, dict):
            return cached
        data = await request_json(
            self._http, "GET", VIACEP_URL.format(cep=digits), service="CEP", not_found_ok=True
        )
        if not isinstance(data, dict) or data.get("erro"):
            raise ToolError(f"CEP {digits[:5]}-{digits[5:]} não encontrado")
        result = {
            "cep": f"{digits[:5]}-{digits[5:]}",
            "street": data.get("logradouro") or None,
            "complement": data.get("complemento") or None,
            "neighborhood": data.get("bairro") or None,
            "city": data.get("localidade") or None,
            "state": data.get("uf") or None,
            "ddd": data.get("ddd") or None,
        }
        self._cache.set(digits, result)
        return result
