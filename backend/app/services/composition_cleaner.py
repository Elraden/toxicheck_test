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
E_CODE_RE = re.compile(r"\b[еe]\s*-?\s*\d{3,4}[a-zа-я]?\b", re.IGNORECASE)
INGREDIENT_HINT_RE = re.compile(
    r"\b("
    r"вода|сахар|соль|мука|молоко|масло|жир|белок|сыворот|"
    r"какао|крахмал|клетчат|лецитин|кислота|сироп|"
    r"ароматизатор|краситель|консервант|эмульгатор|стабилизатор|"
    r"подсластитель|регулятор|загуститель|разрыхлитель"
    r")\w*",
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


def is_probable_composition_text(value: str | None) -> bool:
    if not value:
        return False

    text = clean_raw_ocr_text(value)

    if len(text) < 3:
        return False

    if E_CODE_RE.search(text):
        return True

    if INGREDIENT_HINT_RE.search(text):
        return True

    return "," in text or ";" in text
