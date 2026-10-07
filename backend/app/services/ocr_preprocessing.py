"""
Фаза 3, шаг 3 — адаптивный препроцессинг.

В отличие от прошлой версии (cv_preprocessing.py), здесь обработка
применяется не всегда одинаково, а по измеренным метрикам конкретного
фото:
  - апскейл — по медианной высоте распознанных слов (целевая высота),
    а не фиксированный множитель
  - резкость (unsharp) — только если фото объективно размыто
    (variance of Laplacian ниже порога)
  - контраст (CLAHE) — только если измеренный контраст низкий

Пороги — константы наверху, специально не откалиброваны "на глаз":
скрипт adaptive_test.py печатает сырые метрики рядом с точностью, чтобы
пороги можно было откалибровать по реальным данным, а не догадкам.
"""

import cv2
import numpy as np
import pytesseract

TESSERACT_LANG = "rus"
TESSERACT_CONFIG = "--oem 3 --psm 6"

# --- Целевые параметры (начальные значения, требуют калибровки по данным) ---
TARGET_WORD_HEIGHT_PX = 34   # к какой высоте слова стремимся после апскейла
MAX_UPSCALE_FACTOR = 4.0     # не апскейлить больше этого, даже если метрика просит
MIN_UPSCALE_FACTOR = 1.0     # не даунскейлить, если слова и так крупные

BLUR_VAR_THRESHOLD = 120.0   # ниже — считаем фото размытым, применяем резкость
CONTRAST_STD_THRESHOLD = -1.0  # CLAHE отключена как метод коррекции: и в
# абляционном тесте (upscale+clahe: 0.080 против baseline 0.314), и в этом
# адаптивном тесте (15 случаев с CLAHE, среднее падение -0.250, улучшений
# 0 из 15) она стабильно вредит независимо от условий срабатывания.
# Порог -1.0 гарантирует, что contrast_score < порог никогда не станет True.
# Метрика contrast_score всё ещё считается и логируется, просто не используется
# для принятия решения — вдруг пригодится позже для другого подхода.

STANDARD_WIDTH_FOR_METRICS = 1000  # к этой ширине приводим фото перед замером
# blur/contrast, чтобы метрики были сравнимы между фото разных исходных размеров

MAX_IMAGE_DIMENSION = 2000  # тот же кап, что и в baseline (Фаза 1) — без него фото
# без сработавших условий обработки уходят в OCR в родном разрешении камеры
# (3000-4000px), что даёт непредсказуемый результат, не сравнимый с baseline
MAX_OUTPUT_DIMENSION = 3000
TESSERACT_TIMEOUT_SECONDS = 15


def _resize_to_max_dimension(image: np.ndarray, max_dim: int = MAX_IMAGE_DIMENSION) -> np.ndarray:
    h, w = image.shape[:2]
    longest_side = max(h, w)
    if longest_side <= max_dim:
        return image
    scale = max_dim / longest_side
    return cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def _resize_to_width(gray: np.ndarray, width: int) -> np.ndarray:
    h, w = gray.shape[:2]
    if w == width:
        return gray
    scale = width / w
    if h * scale > MAX_OUTPUT_DIMENSION:
        return _resize_to_max_dimension(gray, MAX_OUTPUT_DIMENSION)
    return cv2.resize(gray, (width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def compute_blur_score(gray: np.ndarray) -> float:
    """Variance of Laplacian на стандартизированном по ширине фото —
    чем ниже, тем более фото размыто."""
    standardized = _resize_to_width(gray, STANDARD_WIDTH_FOR_METRICS)
    return float(cv2.Laplacian(standardized, cv2.CV_64F).var())


def compute_contrast_score(gray: np.ndarray) -> float:
    """Стандартное отклонение яркости — грубая, но быстрая мера контраста."""
    return float(gray.std())


def estimate_median_word_height(gray: np.ndarray) -> float:
    """Быстрый пробный OCR-проход только для замера высоты слов в пикселях.
    Не используется как финальный результат, только для расчёта коэффициента
    апскейла."""
    data = pytesseract.image_to_data(
        gray, lang=TESSERACT_LANG, config=TESSERACT_CONFIG,
        output_type=pytesseract.Output.DICT,
        timeout=TESSERACT_TIMEOUT_SECONDS,
    )
    heights = [
        h for text, conf, h in zip(data["text"], data["conf"], data["height"])
        if text.strip() and conf not in ("-1", -1) and int(float(conf)) > 30
    ]
    if not heights:
        return 0.0
    return float(np.median(heights))


def compute_upscale_factor(median_word_height: float) -> float:
    if median_word_height <= 0:
        return 2.0  # ничего не распозналось на пробном проходе — берём безопасный дефолт
    factor = TARGET_WORD_HEIGHT_PX / median_word_height
    return round(min(max(factor, MIN_UPSCALE_FACTOR), MAX_UPSCALE_FACTOR), 2)


def unsharp_mask(gray: np.ndarray, amount: float = 1.2, radius: int = 3) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (0, 0), radius)
    return cv2.addWeighted(gray, 1 + amount, blurred, -amount, 0)


def enhance_contrast(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


def adaptive_enhance(image: np.ndarray) -> tuple:
    """Возвращает (обработанное_изображение, метрики_dict) — метрики нужны
    для логирования/калибровки, не только для принятия решений внутри.

    Порядок специально такой: сначала базовый ресайз-кап до 2000px (как в
    baseline, на ЦВЕТНОМ изображении, потом grayscale — тем же порядком,
    что и baseline, чтобы при отсутствии дальнейшей обработки результат был
    побайтово идентичен baseline). Все измерения (blur/contrast/высота
    слова) считаются уже на этой базе, а не на исходном разрешении камеры:
    иначе пробный OCR-проход для замера высоты слова гоняется на
    3000-4000px и съедает время, которое не попадает в замеры скрипта
    (таймер считает только финальный OCR-вызов), искажая честную оценку
    скорости."""
    base_color = _resize_to_max_dimension(image)
    base_gray = cv2.cvtColor(base_color, cv2.COLOR_BGR2GRAY) if len(base_color.shape) == 3 else base_color

    blur_score = compute_blur_score(base_gray)
    contrast_score = compute_contrast_score(base_gray)
    median_word_height = estimate_median_word_height(base_gray)
    upscale_factor = compute_upscale_factor(median_word_height)
    upscale_factor = min(upscale_factor, MAX_OUTPUT_DIMENSION / max(base_gray.shape[:2]))

    if upscale_factor > 1.05:
        h, w = base_gray.shape[:2]
        result = cv2.resize(base_gray, (int(w * upscale_factor), int(h * upscale_factor)), interpolation=cv2.INTER_CUBIC)
    else:
        result = base_gray

    applied_clahe = contrast_score < CONTRAST_STD_THRESHOLD
    if applied_clahe:
        result = enhance_contrast(result)

    # Резкость больше не зависит только от независимого порога blur_score:
    # апскейл (INTER_CUBIC) сам по себе смягчает картинку интерполяцией, и
    # это подтверждено данными — среди апскейленных фото у ухудшившихся
    # (без резкости) медианный blur_score был ВЫШЕ (454, то есть фото и так
    # были достаточно резкими), чем у улучшившихся (332). Апскейл без
    # компенсирующей резкости не столько помогает мелкому тексту, сколько
    # портит то, что и так было резким. Поэтому резкость теперь применяется
    # как обязательная компенсация после любого апскейла, а не только когда
    # blur_score сам по себе низкий.
    applied_sharpen = blur_score < BLUR_VAR_THRESHOLD or upscale_factor > 1.05
    if applied_sharpen:
        result = unsharp_mask(result)

    metrics = {
        "blur_score": round(blur_score, 1),
        "contrast_score": round(contrast_score, 1),
        "median_word_height_px": round(median_word_height, 1),
        "upscale_factor": upscale_factor,
        "applied_clahe": applied_clahe,
        "applied_sharpen": applied_sharpen,
    }
    return result, metrics
