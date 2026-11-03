import re

from jambachat.expenses.models import Category
from jambachat.text import fold

RULES: tuple[tuple[Category, tuple[str, ...]], ...] = (
    (
        Category.ASSINATURAS,
        (
            "netflix",
            "spotify",
            "disney",
            "prime video",
            "amazon prime",
            "youtube premium",
            "hbo",
            "globoplay",
            "deezer",
            "icloud",
            "apple com bill",
            "google one",
            "chatgpt",
            "openai",
        ),
    ),
    (
        Category.VIAGEM,
        ("hotel", "airbnb", "booking", "latam", "gol linhas", "azul linhas", "decolar"),
    ),
    (Category.PETS, ("petz", "cobasi", "petshop", "pet shop", "veterinaria", "veterinario")),
    (
        Category.TRANSPORTE,
        (
            "uber",
            "99app",
            "99pop",
            "99 pop",
            "cabify",
            "posto",
            "shell",
            "ipiranga",
            "petrobras",
            "combustivel",
            "estacionamento",
            "sem parar",
            "conectcar",
            "veloe",
            "metro",
            "cptm",
            "sptrans",
        ),
    ),
    (
        Category.ALIMENTACAO,
        (
            "ifood",
            "rappi",
            "restaurante",
            "lanchonete",
            "padaria",
            "pizzaria",
            "burger",
            "mcdonalds",
            "mc donalds",
            "subway",
            "starbucks",
            "cafe",
            "cafeteria",
            "outback",
            "habibs",
        ),
    ),
    (
        Category.COMPRAS,
        (
            "mercado livre",
            "mercadolivre",
            "amazon",
            "shopee",
            "magalu",
            "magazine luiza",
            "americanas",
            "casas bahia",
            "shein",
            "renner",
            "riachuelo",
            "zara",
            "kabum",
        ),
    ),
    (
        Category.MERCADO,
        (
            "supermercado",
            "mercado",
            "atacadao",
            "assai",
            "carrefour",
            "pao de acucar",
            "hortifruti",
            "sams club",
            "makro",
        ),
    ),
    (
        Category.SAUDE,
        (
            "drogasil",
            "droga raia",
            "drogaria",
            "farmacia",
            "pague menos",
            "hospital",
            "clinica",
            "laboratorio",
            "smart fit",
            "smartfit",
            "academia",
        ),
    ),
    (Category.EDUCACAO, ("udemy", "alura", "coursera", "livraria", "escola", "faculdade")),
    (
        Category.LAZER,
        ("cinema", "cinemark", "ingresso", "sympla", "steam", "playstation", "xbox", "nintendo"),
    ),
    (
        Category.CONTAS,
        ("enel", "sabesp", "cemig", "copel", "comgas", "vivo", "claro", "tim", "light"),
    ),
)

_PATTERNS = tuple(
    (category, re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b"))
    for category, words in RULES
)


def guess_category(text: str) -> Category:
    folded = re.sub(r"[^a-z0-9 ]+", " ", fold(text))
    for category, pattern in _PATTERNS:
        if pattern.search(folded):
            return category
    return Category.OUTROS
