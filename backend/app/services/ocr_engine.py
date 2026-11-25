"""Image recognition adapted from the ML prototype; catalog access stays in the API."""

import base64
import re
from io import BytesIO
from typing import Optional

import cv2
import numpy as np
import pytesseract
import Levenshtein
from PIL import Image

from app.services.ocr_preprocessing import adaptive_enhance

TESSERACT_LANG = "rus"
TESSERACT_CONFIG = "--oem 3 --psm 6"
CONFIDENCE_RETAKE_THRESHOLD = 0.45
OCR_VERSION = "embedded-ml-20261110"

def decode_base64_image(base64_string: str) -> Optional[np.ndarray]:
    try:
        image_bytes = base64.b64decode(base64_string, validate=True)
        if len(image_bytes) > 12 * 1024 * 1024:
            return None
        # Inspect dimensions before OpenCV allocates the decoded pixel buffer.
        with Image.open(BytesIO(image_bytes)) as header:
            if header.format not in {"JPEG", "PNG", "WEBP"} or header.width * header.height > 25_000_000:
                return None
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        return cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    except Exception:
        return None


def reconstruct_text_from_data(data: dict) -> str:
    lines: dict = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
    return "\n".join(" ".join(words) for words in lines.values())


def run_ocr(gray_image: np.ndarray) -> tuple:
    data = pytesseract.image_to_data(
        gray_image, lang=TESSERACT_LANG, config=TESSERACT_CONFIG,
        output_type=pytesseract.Output.DICT,
        timeout=25,
    )
    raw_text = reconstruct_text_from_data(data)
    confidences = [float(c) for word, c in zip(data["text"], data["conf"])
                   if word.strip() and float(c) >= 0]
    mean_confidence = round(sum(confidences) / len(confidences) / 100, 3) if confidences else 0.0
    return raw_text, mean_confidence


# ---------------------------------------------------------------------
# Извлечение блока состава и блока аллергенов (Фаза 1, без изменений)
# ---------------------------------------------------------------------

COMPOSITION_START_CANDIDATES = ["состав", "ингредиенты"]

# "следы" и "аллерген" добавлены отдельно от "может"/"содержит": реальные
# этикетки нередко оформляют этот блок без слов "может содержать" вообще —
# либо отдельным заголовком ("Аллергены:", "Пищевые аллергены:"), либо
# короткой формулировкой без глагола ("Следы арахиса возможны.").
ALLERGEN_START_CANDIDATES = ["содержит", "содержать", "может", "аллерген", "следы"]

# Отдельная формулировка предупреждения о перекрёстном загрязнении:
# "Произведено на предприятии, где используются [аллергены]" — не содержит
# слов "может"/"содержит" вообще, поэтому старая граница её не ловила, и
# такой текст утекал в extracted_composition, а перечисленные там аллергены
# (например, арахис, горчица) матчились как настоящие ингредиенты состава
# с уверенностью 1.0 — хотя по смыслу это предупреждение о возможных следах
# из-за общего производства, а не гарантированный ингредиент ЭТОГО продукта.
# Разница критична для продукта, отвечающего за безопасность состава.
# "упаковано"/"вырабатывается" — те же варианты формулировки с другим
# глаголом ("Упаковано на производстве, где также используются...",
# "Продукт вырабатывается на линии, где применяются...").
#
# НЕ добавляем сюда "линии"/"линия" — несмотря на то что реальная
# формулировка "на одной линии, где используются..." встречается на
# практике. При допуске Левенштейна 2 для коротких слов "линии" слишком
# похоже на "малины" (частый реальный ингредиент в составах соков — см.
# ground truth этого проекта), и живой сок с малиновым соком рисковал бы
# обрезать состав посередине предложения. Эта формулировка жертвуется ради
# безопасности остальных случаев.
FACILITY_WARNING_START_CANDIDATES = [
    "произведено", "производстве", "предприятии", "упаковано", "вырабатывается",
]

# Раньше проверялись буквальным text.lower().find() — без учёта гомоглифов
# и опечаток OCR. То есть граница по аллергенам/перекрёстному загрязнению
# была устойчива к шуму (через find_fuzzy_boundary), а граница конца
# состава — нет: любая OCR-опечатка в "пищевая ценность" (частое явление,
# это всегда мелкий шрифт в самом низу этикетки) просто не находилась, и
# весь "хвост" этикетки (хранение, срок годности, адрес производителя)
# утекал в extracted_composition как единый нераспознанный токен.
# Переведено на тот же механизм нечёткого поиска, что и остальные границы,
# — поэтому здесь не фразы, а их самые характерные ОДНИМ словом части:
# "пищевая"/"энергетическая" не взяты отдельно, потому что слово "пищевая"
# само по себе сплошь и рядом встречается ВНУТРИ состава ("ароматизатор
# пищевой", "добавка пищевая") — это обрезало бы состав на первом же таком
# ингредиенте.
COMPOSITION_END_MARKERS = [
    "ценность",       # "пищевая ценность", "энергетическая ценность"
    "хранения",        # "условия хранения", "срок хранения"
    "хранить",         # "хранить при..."
    "годности",        # "срок годности"
    "нетто",           # "масса нетто"
    "калорийность",
    "изготовитель",
    "производитель",
    "наименование",    # "наименование и адрес изготовителя"
    "импортер", "импортёр",
    "изготовления",    # "дата изготовления" — отдельно от "изготовитель":
    # слова похожи, но различаются суффиксом сильнее, чем допускает
    # Левенштейн-порог для "изготовитель", так что без отдельного слова
    # "дата изготовления" не ловилась бы вообще.
]

# Слово-довесок типичных двусловных маркеров ("срок годности", "масса
# нетто", "условия хранения", "пищевая/энергетическая ценность", "дата
# изготовления"). Сам маркер выше матчится по второму слову — если первое
# слово стоит прямо перед найденной границей без знака пунктуации между
# ними, оно прилипает к последнему токену состава/аллергенов и портит его
# сопоставление со словарём ("...сахар Срок" вместо "...сахар"). Проверяем
# это ТОЛЬКО для слова, которое оказалось непосредственно перед уже
# найденной границей — поэтому "пищевая" тут не опаснее обычного: внутри
# самого состава ("ароматизатор пищевой") оно не встречается вплотную перед
# этими маркерами.
_BOUNDARY_PREFIX_WORDS = ["срок", "масса", "условия", "энергетическая", "пищевая", "дата"]

# Таблица пищевой ценности иногда идёт БЕЗ слова "пищевая ценность" вообще —
# сразу строкой "Белки: 5,2 г Жиры: 3,1 г Углеводы: 12 г". Добавить
# "белки"/"жиры"/"углеводы" в COMPOSITION_END_MARKERS как обычные fuzzy-слова
# нельзя — у них слишком много похожих по буквам настоящих ингредиентов
# ("жир кондитерский", "белок яичный"), и нечёткое совпадение резало бы
# состав не там. Поэтому ищем не слово само по себе, а характерный для
# таблицы паттерн "слово сразу перед числом" — обычная формулировка состава
# так не выглядит.
NUTRITION_TABLE_PATTERN = re.compile(
    r"\b(белки|жиры|углеводы)\b\s*[:\-—]?\s*\d", re.IGNORECASE
)

_HOMOGLYPH_TABLE = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
})


def _normalize_homoglyphs(text: str) -> str:
    return text.translate(_HOMOGLYPH_TABLE)


def _find_fuzzy_match(text: str, candidates: list, search_from: int = 0):
    normalized = _normalize_homoglyphs(text).lower()
    best_match = None
    for m in re.finditer(r"[а-яё]+", normalized):
        if m.start() < search_from:
            continue
        word = m.group()
        for candidate in candidates:
            max_dist = 1 if len(candidate) <= 5 else 2 if len(candidate) <= 7 else 3
            if abs(len(word) - len(candidate)) > max_dist:
                continue
            if Levenshtein.distance(word, candidate) <= max_dist:
                if best_match is None or m.start() < best_match[0]:
                    best_match = (m.start(), m.end())
    return best_match


def find_section_start(text: str, candidates: list, search_from: int = 0):
    match = _find_fuzzy_match(text, candidates, search_from)
    return match[1] if match else None


def find_fuzzy_boundary(text: str, candidates: list, search_from: int = 0):
    match = _find_fuzzy_match(text, candidates, search_from)
    return match[0] if match else None


def find_nutrition_table_boundary(text: str, search_from: int = 0):
    """Ищет паттерн "Белки/Жиры/Углеводы + число" — см. комментарий у
    NUTRITION_TABLE_PATTERN. В отличие от остальных границ, это не
    fuzzy-поиск по словам: нужна именно связка слова с числом, иначе
    ложные срабатывания на реальных ингредиентах ("жир кондитерский")."""
    normalized = _normalize_homoglyphs(text)
    match = NUTRITION_TABLE_PATTERN.search(normalized, search_from)
    return match.start() if match else None


def _trim_boundary_prefix(text: str, end_idx: Optional[int]) -> Optional[int]:
    """См. комментарий у _BOUNDARY_PREFIX_WORDS: если прямо перед найденной
    границей стоит слово-довесок устойчивого двусловного маркера без
    пунктуации между ними, сдвигает границу назад, чтобы оно не прилипло к
    последнему токену."""
    if end_idx is None:
        return end_idx
    before = text[:end_idx]
    m = re.search(r"([а-яёА-ЯЁ]+)\s*$", before)
    if not m:
        return end_idx
    word = _normalize_homoglyphs(m.group(1)).lower()
    for prefix in _BOUNDARY_PREFIX_WORDS:
        max_dist = 2 if len(prefix) <= 7 else 3
        if abs(len(word) - len(prefix)) <= max_dist and Levenshtein.distance(word, prefix) <= max_dist:
            return m.start(1)
    return end_idx


def _earliest_end_boundary(text: str, start_idx: int, *extra_boundaries) -> Optional[int]:
    """Общая логика для конца блока (состава или аллергенов): берёт
    самую раннюю из всех найденных границ-кандидатов после start_idx."""
    candidates = [
        find_fuzzy_boundary(text, ALLERGEN_START_CANDIDATES, search_from=start_idx),
        find_fuzzy_boundary(text, FACILITY_WARNING_START_CANDIDATES, search_from=start_idx),
        find_fuzzy_boundary(text, COMPOSITION_END_MARKERS, search_from=start_idx),
        find_nutrition_table_boundary(text, search_from=start_idx),
        *extra_boundaries,
    ]
    found = [c for c in candidates if c is not None]
    return min(found) if found else None


def extract_composition_block(raw_text: str) -> str:
    text = raw_text.replace("\n", " ")
    start_idx = find_section_start(text, COMPOSITION_START_CANDIDATES)
    if start_idx is None:
        start_idx = 0
    end_idx = _trim_boundary_prefix(text, _earliest_end_boundary(text, start_idx))
    segment = text[start_idx:end_idx] if end_idx is not None else text[start_idx:]
    return segment.strip(" :–—-.,;\"'«»_—|\\")


def extract_allergens_block(raw_text: str) -> str:
    text = raw_text.replace("\n", " ")
    composition_start = find_section_start(text, COMPOSITION_START_CANDIDATES)
    search_from = composition_start if composition_start is not None else 0
    start_idx = find_section_start(text, ALLERGEN_START_CANDIDATES, search_from=search_from)
    if start_idx is None:
        # Формулировка "может/содержит/аллерген/следы" не нашлась — пробуем
        # формулировку про перекрёстное загрязнение ("произведено на
        # предприятии, где используются..."), чтобы это предупреждение
        # попало в allergens, а не потерялось и не утекло в состав как
        # настоящие ингредиенты.
        start_idx = find_section_start(text, FACILITY_WARNING_START_CANDIDATES, search_from=search_from)
        if start_idx is None:
            return ""
    # Конец блока аллергенов ищем теми же маркерами конца, что и у состава
    # (пищевая ценность/срок хранения/изготовитель/...), но НЕ ограничиваем
    # его границами ALLERGEN_START_CANDIDATES/FACILITY_WARNING_START_CANDIDATES
    # самих по себе — иначе в длинном предложении вида "может содержать
    # следы..." слово "следы" само себя же обрежет.
    end_idx_candidates = [
        find_fuzzy_boundary(text, COMPOSITION_END_MARKERS, search_from=start_idx),
        find_nutrition_table_boundary(text, search_from=start_idx),
    ]
    end_idx_found = [c for c in end_idx_candidates if c is not None]
    end_idx = _trim_boundary_prefix(text, min(end_idx_found) if end_idx_found else None)
    tail = text[start_idx:end_idx] if end_idx is not None else text[start_idx:]
    return tail.lstrip(" :–—-.").strip(" .,:;\"'«»_—|\\")
