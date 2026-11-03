from jambachat.expenses.models import Category
from jambachat.finance.categories import map_category


def test_maps_aggregator_categories() -> None:
    assert map_category("Food") == Category.ALIMENTACAO
    assert map_category("supermercado") == Category.MERCADO


def test_unknown_category_falls_back_to_outros() -> None:
    assert map_category("unknown") == Category.OUTROS
    assert map_category(None) == Category.OUTROS
