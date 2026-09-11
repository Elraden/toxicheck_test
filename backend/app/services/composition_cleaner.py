import re

SPACE_RE = re.compile(r"\s+")
SEPARATOR_RE = re.compile(r"\s*[,;]\s*")
LEADING_LABEL_RE = re.compile(
    r"^\s*(состав|ингредиенты)\s*[:\-–—.]?\s*",
    re.IGNORECASE,
)
END_MARKERS_RE = re.compile(
    r"\b("
    r"пищевая\s+ценность|энергетическая\s+ценность|"
    r"срок\s+годности|условия\s+хранения|хранить\s+при|"
    r"масса\s+нетто|изготовитель|производитель"
    r")\b",
    re.IGNORECASE,
)


def clean_composition_text(value: str | None) -> str:
    if not value:
        return ""

    cleaned = value.replace("\r", " ").replace("\n", " ")
    cleaned = cleaned.replace("•", ",").replace("|", " ")
    cleaned = cleaned.replace("Е ", "E ")
    cleaned = LEADING_LABEL_RE.sub("", cleaned)

    marker = END_MARKERS_RE.search(cleaned)
    if marker:
        cleaned = cleaned[: marker.start()]

    cleaned = SEPARATOR_RE.sub(", ", cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned)

    return cleaned.strip(" ,;:.-–—")


def clean_raw_ocr_text(value: str | None) -> str:
    if not value:
        return ""

    return SPACE_RE.sub(" ", value).strip()
