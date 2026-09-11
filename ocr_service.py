"""
Baseline OCR-сервис (Фаза 1, без препроцессинга) - HTTP API для
теста интеграции с backend на хороших фото

Запуск:
    uvicorn ocr_service:app --host 0.0.0.0 --port 8000


Пример запроса:
    curl -X POST http://localhost:8000/recognize \\
      -H "Content-Type: application/json" \\
      -d '{
            "request_id": "test-1",
            "image": "<base64 JPEG>",
            "image_format": "jpeg"
          }'
"""

import base64
import re
import time
from typing import Optional

import cv2
import numpy as np
import pytesseract
import Levenshtein
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ToxiCheck OCR Service (baseline, no CV preprocessing)")

# Настройки OCR
TESSERACT_LANG = "rus"
TESSERACT_CONFIG = "--oem 3 --psm 6"  # oem 3 = LSTM, psm 6 = единый блок текста
MAX_IMAGE_DIMENSION = 2000  # ресайз больших фото для скорости

# Настройки извлечения состава/аллергенов из сырого текста
COMPOSITION_START_CANDIDATES = ["состав", "ингредиенты"]
ALLERGEN_START_CANDIDATES = ["содержит", "содержать", "может"]
COMPOSITION_END_MARKERS = [
    "пищевая ценность", "энергетическая ценность", "условия хранения",
    "срок годности", "масса нетто", "хранить при", "калорийность",
]

# Гомоглифы: OCR капсом иногда путает латиницу и кириллицу визуально
# похожих букв - нормализуем перед нечётким поиском заголовков.
_HOMOGLYPH_TABLE = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
})



# Pydantic-модели: описывают контракт запроса/ответа для FastAPI
# (автоматическая валидация + автогенерация /docs)


class RecognizeContext(BaseModel):
    """Необязательный контекст запроса — для трассировки/аналитики,
    на распознавание не влияет."""
    product_id: Optional[str] = None
    source: Optional[str] = None


class RecognizeRequest(BaseModel):
    """Формат запроса от backend, см. ocr_cv_api_contract.md."""
    request_id: str
    image: str  # base64-encoded JPEG/PNG
    image_format: str = "jpeg"
    locale: str = "ru"
    context: Optional[RecognizeContext] = None


class OcrBlock(BaseModel):
    raw_text: str
    ocr_confidence: float


class ErrorBlock(BaseModel):
    code: str
    message: str


class RecognizeResponse(BaseModel):
    """Формат ответа. Поле `ingredients` пока не
    заполняется - оно появится, когда будет готово дерево решений
    и словарь нормализации. Сейчас возвращаем то, что уже реализовано:
    сырой текст, извлечённый состав и аллергены."""
    request_id: str
    status: str  # "success" | "error"
    processing_time_ms: int
    ocr: Optional[OcrBlock] = None
    extracted_composition: Optional[str] = None
    extracted_allergens: Optional[str] = None
    error: Optional[ErrorBlock] = None


# Декодирование изображения

def decode_base64_image(base64_string: str) -> Optional[np.ndarray]:
    """Декодирует base64-строку в изображение OpenCV (BGR numpy-массив).
    Возвращает None, если строка повреждена или это не валидное изображение
    (например, обрезанный base64 или не тот формат файла)."""
    try:
        image_bytes = base64.b64decode(base64_string)
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        return image
    except Exception:
        return None


# OCR

def resize_if_needed(image: np.ndarray) -> np.ndarray:
    """Уменьшает слишком большие фото (типично для современных телефонов,
    3000-4000px по стороне) до MAX_IMAGE_DIMENSION - без этого OCR работает
    в разы медленнее почти без выигрыша в точности."""
    height, width = image.shape[:2]
    longest_side = max(height, width)
    if longest_side <= MAX_IMAGE_DIMENSION:
        return image
    scale = MAX_IMAGE_DIMENSION / longest_side
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


def reconstruct_text_from_data(data: dict) -> str:
    """Собирает читаемый текст из результата pytesseract.image_to_data,
    группируя распознанные слова по строкам (block/par/line). Так же
    удобно получить и текст, и confidence из одного вызова Tesseract,
    вместо двух отдельных вызовов (image_to_string + image_to_data),
    что вдвое быстрее (см. оптимизацию в Фазе 1)."""
    lines: dict = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
    return "\n".join(" ".join(words) for words in lines.values())


def run_ocr(gray_image: np.ndarray) -> tuple:
    """Запускает Tesseract на чёрно-белом изображении. Возвращает
    (сырой_текст, средний_confidence). Один вызов image_to_data вместо
    отдельных вызовов для текста и confidence."""
    data = pytesseract.image_to_data(
        gray_image, lang=TESSERACT_LANG, config=TESSERACT_CONFIG,
        output_type=pytesseract.Output.DICT,
    )
    raw_text = reconstruct_text_from_data(data)
    confidences = [int(c) for c in data["conf"] if c not in ("-1", -1)]
    mean_confidence = round(sum(confidences) / len(confidences) / 100, 3) if confidences else 0.0
    return raw_text, mean_confidence


# Извлечение блока состава и блока аллергенов из сырого OCR-текста


def _normalize_homoglyphs(text: str) -> str:
    """Заменяет латинские буквы, визуально похожие на кириллические
    (частая ошибка OCR на капсом-этикетках), на кириллические аналоги.
    Замена посимвольная - длина строки не меняется, индексы остаются
    верными относительно исходного текста."""
    return text.translate(_HOMOGLYPH_TABLE)


def _find_fuzzy_match(text: str, candidates: list, search_from: int = 0):
    """Ищет в тексте слово, похожее на одно из candidates (расстояние
    Левенштейна в пределах порога, зависящего от длины слова), начиная
    с позиции search_from. Нужно, чтобы находить заголовки вроде 'Состав'
    даже если OCR распознал их с опечаткой. Возвращает (start, end)
    самого левого совпадения в исходной строке, либо None."""
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
    """Возвращает индекс КОНЦА найденного слова-заголовка (текст после
    заголовка начинается с этого индекса), либо None."""
    match = _find_fuzzy_match(text, candidates, search_from)
    return match[1] if match else None


def find_fuzzy_boundary(text: str, candidates: list, search_from: int = 0):
    """Возвращает индекс НАЧАЛА найденного слова - используется, чтобы
    обрезать текст ПЕРЕД этим словом (например, перед 'может содержать',
    которое относится уже к аллергенам, а не к составу)."""
    match = _find_fuzzy_match(text, candidates, search_from)
    return match[0] if match else None


def extract_composition_block(raw_text: str) -> str:
    """Вырезает из полного OCR-текста этикетки только блок состава: от
    заголовка 'Состав'/'Ингредиенты' до ближайшей границы — явного
    маркера конца (пищевая ценность, срок годности и т.д.) или начала
    блока аллергенов ('может содержать'/'содержит'), в зависимости от
    того, что встретилось раньше. Если заголовок не найден вообще,
    возвращает весь текст как есть."""
    text = raw_text.replace("\n", " ")

    start_idx = find_section_start(text, COMPOSITION_START_CANDIDATES)
    if start_idx is None:
        return text

    end_idx = find_fuzzy_boundary(text, ALLERGEN_START_CANDIDATES, search_from=start_idx)
    lowered = text.lower()
    for marker in COMPOSITION_END_MARKERS:
        pos = lowered.find(marker, start_idx)
        if pos != -1 and (end_idx is None or pos < end_idx):
            end_idx = pos

    segment = text[start_idx:end_idx] if end_idx is not None else text[start_idx:]
    return segment.strip(" :–—-.,;\"'«»_—|\\")


def extract_allergens_block(raw_text: str) -> str:
    """Вырезает блок аллергенов ('Содержит глютен...'/'Может содержать
    следы...') отдельным полем от состава - это предупреждение, а не
    ингредиент, и в продукте должно уходить в модуль персонализации, а
    не в общий состав. Поиск ограничен текстом ПОСЛЕ заголовка состава,
    чтобы случайные совпадения в тексте до 'Состав:' (например, в адресе
    производителя) не давали ложных срабатываний. Возвращает пустую
    строку, если на этикетке такого блока нет."""
    text = raw_text.replace("\n", " ")

    composition_start = find_section_start(text, COMPOSITION_START_CANDIDATES)
    search_from = composition_start if composition_start is not None else 0

    start_idx = find_section_start(text, ALLERGEN_START_CANDIDATES, search_from=search_from)
    if start_idx is None:
        return ""

    tail = text[start_idx:].lstrip(" :–—-.")
    positions = [pos for pos in (tail.lower().find(m) for m in COMPOSITION_END_MARKERS) if pos != -1]
    if positions:
        tail = tail[:min(positions)]

    return tail.strip(" .,:;\"'«»_—|\\")


# HTTP-эндпоинты

@app.get("/health")
def health_check():
    """Проверка сервиса."""
    return {"status": "ok"}


@app.post("/recognize", response_model=RecognizeResponse)
def recognize(request: RecognizeRequest) -> RecognizeResponse:
    """Основной эндпоинт: принимает фото этикетки в base64, возвращает
    сырой текст + извлечённые состав и аллергены. Без CV-препроцессинга
    и без сопоставления с базой ингредиентов - упрощённая версия для теста интеграции на хороших фото."""
    start_time = time.time()

    image = decode_base64_image(request.image)
    if image is None:
        return RecognizeResponse(
            request_id=request.request_id,
            status="error",
            processing_time_ms=round((time.time() - start_time) * 1000),
            error=ErrorBlock(code="IMAGE_UNREADABLE", message="Не удалось декодировать изображение"),
        )

    try:
        resized = resize_if_needed(image)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        raw_text, confidence = run_ocr(gray)
    except Exception as exc:
        return RecognizeResponse(
            request_id=request.request_id,
            status="error",
            processing_time_ms=round((time.time() - start_time) * 1000),
            error=ErrorBlock(code="INTERNAL_ERROR", message=str(exc)),
        )

    composition = extract_composition_block(raw_text)
    allergens = extract_allergens_block(raw_text)

    return RecognizeResponse(
        request_id=request.request_id,
        status="success",
        processing_time_ms=round((time.time() - start_time) * 1000),
        ocr=OcrBlock(raw_text=raw_text, ocr_confidence=confidence),
        extracted_composition=composition,
        extracted_allergens=allergens,
    )