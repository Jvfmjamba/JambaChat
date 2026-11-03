import json

import httpx

from jambachat.tools.catalog import build_registry
from tests.fakes import make_settings


async def test_tool_schemas_are_flat_for_picky_providers() -> None:
    async with httpx.AsyncClient() as http:
        specs = build_registry(make_settings(tavily_api_key="k"), http).specs()
    for spec in specs:
        dumped = json.dumps(spec.parameters)
        assert spec.parameters["type"] == "object", spec.name
        for forbidden in ('"$ref"', '"$defs"', '"anyOf"', '"title"', '"default": null'):
            assert forbidden not in dumped, (spec.name, forbidden)
    names = [spec.name for spec in specs]
    assert len(names) == len(set(names))


def test_budget_category_enum_includes_total() -> None:
    [spec] = [s for s in build_registry().specs() if s.name == "set_budget"]
    enum = spec.parameters["properties"]["category"]["enum"]
    assert enum[-1] == "Total"
    assert "Alimentação" in enum
