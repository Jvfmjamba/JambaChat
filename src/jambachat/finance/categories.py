from jambachat.expenses.models import Category
from jambachat.text import fold

_CATEGORIES = {
    "alimentacao": Category.ALIMENTACAO,
    "food": Category.ALIMENTACAO,
    "restaurantes": Category.ALIMENTACAO,
    "mercado": Category.MERCADO,
    "supermercado": Category.MERCADO,
    "groceries": Category.MERCADO,
    "transporte": Category.TRANSPORTE,
    "transport": Category.TRANSPORTE,
    "moradia": Category.MORADIA,
    "housing": Category.MORADIA,
    "saude": Category.SAUDE,
    "health": Category.SAUDE,
    "lazer": Category.LAZER,
    "entertainment": Category.LAZER,
    "educacao": Category.EDUCACAO,
    "education": Category.EDUCACAO,
    "assinaturas": Category.ASSINATURAS,
    "subscriptions": Category.ASSINATURAS,
    "compras": Category.COMPRAS,
    "shopping": Category.COMPRAS,
    "contas": Category.CONTAS,
    "utilities": Category.CONTAS,
    "pets": Category.PETS,
    "viagem": Category.VIAGEM,
    "travel": Category.VIAGEM,
    "salario": Category.SALARIO,
    "salary": Category.SALARIO,
    "investimentos": Category.INVESTIMENTOS,
    "investments": Category.INVESTIMENTOS,
}


def map_category(category: str | None) -> Category:
    if not category:
        return Category.OUTROS
    return _CATEGORIES.get(fold(category).strip(), Category.OUTROS)
