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
ALLERGEN_START_CANDIDATES = ["содержит", "содержать", "может"]

# Отдельная формулировка предупреждения о перекрёстном загрязнении:
# "Произведено на предприятии, где используются [аллергены]" — не содержит
# слов "может"/"содержит" вообще, поэтому старая граница её не ловила, и
# такой текст утекал в extracted_composition, а перечисленные там аллергены
# (например, арахис, горчица) матчились как настоящие ингредиенты состава
# с уверенностью 1.0 — хотя по смыслу это предупреждение о возможных следах
# из-за общего производства, а не гарантированный ингредиент ЭТОГО продукта.
# Разница критична для продукта, отвечающего за безопасность состава.
FACILITY_WARNING_START_CANDIDATES = ["произведено", "производстве", "предприятии"]
COMPOSITION_END_MARKERS = [
    "пищевая ценность", "энергетическая ценность", "условия хранения",
    "срок годности", "масса нетто", "хранить при", "калорийность",
]
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
            max_dist = 2 if len(candidate) <= 7 else 3
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


def extract_composition_block(raw_text: str) -> str:
    text = raw_text.replace("\n", " ")
    start_idx = find_section_start(text, COMPOSITION_START_CANDIDATES)
    if start_idx is None:
        start_idx = 0
    end_idx = find_fuzzy_boundary(text, ALLERGEN_START_CANDIDATES, search_from=start_idx)

    # Ещё одна граница — предупреждение о перекрёстном загрязнении, у
    # которого своя формулировка, не пересекающаяся с "может/содержит"
    # (см. комментарий у FACILITY_WARNING_START_CANDIDATES выше).
    facility_boundary = find_fuzzy_boundary(text, FACILITY_WARNING_START_CANDIDATES, search_from=start_idx)
    if facility_boundary is not None and (end_idx is None or facility_boundary < end_idx):
        end_idx = facility_boundary

    lowered = text.lower()
    for marker in COMPOSITION_END_MARKERS:
        pos = lowered.find(marker, start_idx)
        if pos != -1 and (end_idx is None or pos < end_idx):
            end_idx = pos
    segment = text[start_idx:end_idx] if end_idx is not None else text[start_idx:]
    return segment.strip(" :–—-.,;\"'«»_—|\\")


def extract_allergens_block(raw_text: str) -> str:
    text = raw_text.replace("\n", " ")
    composition_start = find_section_start(text, COMPOSITION_START_CANDIDATES)
    search_from = composition_start if composition_start is not None else 0
    start_idx = find_section_start(text, ALLERGEN_START_CANDIDATES, search_from=search_from)
    if start_idx is None:
        # Формулировка "может/содержит" не нашлась — пробуем формулировку
        # про перекрёстное загрязнение ("произведено на предприятии, где
        # используются..."), чтобы это предупреждение попало в allergens,
        # а не потерялось и не утекло в состав как настоящие ингредиенты.
        start_idx = find_section_start(text, FACILITY_WARNING_START_CANDIDATES, search_from=search_from)
        if start_idx is None:
            return ""
    tail = text[start_idx:].lstrip(" :–—-.")
    positions = [pos for pos in (tail.lower().find(m) for m in COMPOSITION_END_MARKERS) if pos != -1]
    if positions:
        tail = tail[:min(positions)]
    return tail.strip(" .,:;\"'«»_—|\\")
