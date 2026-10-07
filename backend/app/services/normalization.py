import re
import unicodedata

E_CODE_RE = re.compile(
    r"(?<![\w])[eе][\s-]?(\d{3,4}[a-zа-я]?)(\s*\([ivx]+\))?(?![\w]|\s*\([ivx])", re.IGNORECASE
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
    subtype = re.sub(r"\s+", "", match.group(2) or "").lower()
    return f"E{suffix}{subtype}"


def split_ingredients_text(value: str) -> list[str]:
    # Flatten compound ingredients, but preserve E-code subtype parentheses.
    subtype_positions = set()
    # Keep malformed subtype text intact too: E450(ixyz) must not become E450.
    protected_codes = re.compile(
        r"(?<!\w)[eе][\s-]?\d{3,4}[a-zа-я]?\s*\([ivx][^,;)]*\)?", re.IGNORECASE
    )
    for match in protected_codes.finditer(value):
        subtype_positions.update(range(match.start(), match.end()))
    flattened = "".join(
        "," if char in "()[]{}" and index not in subtype_positions else char
        for index, char in enumerate(value)
    )
    return [
        part.strip()
        for part in SEPARATORS_RE.split(flattened)
        if part.strip()
    ]
