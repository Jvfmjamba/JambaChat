import unicodedata


def fold(text: str) -> str:
    """Minúsculas sem acentos e com espaços normalizados, para comparar textos do usuário."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())
