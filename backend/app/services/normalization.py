import re
import unicodedata

E_CODE_RE = re.compile(
    r"(?<![\w])[eе][\s-]?(\d{3,4}[a-zа-я]?)(?![\w])", re.IGNORECASE
)
SEPARATORS_RE = re.compile(r"[,;]\s*")
SPACES_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.casefold()
    normalized = normalized.replace("ё", "е")
    normalized = re.sub(r"[‐‑‒–—−-]+", " ", normalized)
    normalized = re.sub(r"[^\w\s]+", " ", normalized, flags=re.UNICODE)
    return SPACES_RE.sub(" ", normalized).strip()


def normalize_e_code(value: str) -> str | None:
    match = E_CODE_RE.search(value)

    if not match:
        return None

    suffix = match.group(1).lower().translate(str.maketrans({"а": "a", "б": "b", "в": "b", "с": "c"}))
    return f"E{suffix}"


def split_ingredients_text(value: str) -> list[str]:
    return [
        part.strip()
        for part in SEPARATORS_RE.split(value)
        if part.strip()
    ]
