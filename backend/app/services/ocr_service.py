import base64
import asyncio
import logging
import threading
import time
import uuid
from types import ModuleType

import httpx

from app.core.config import settings
from app.schemas.ocr import OcrResult
from app.services.composition_cleaner import (
    clean_composition_text,
    clean_raw_ocr_text,
    is_probable_composition_text,
)

logger = logging.getLogger(__name__)
_embedded_lock = threading.Lock()


def _strip_data_url(image_base64: str) -> tuple[str, str]:
    if "," not in image_base64 or not image_base64.startswith("data:"):
        return image_base64, "jpeg"

    header, payload = image_base64.split(",", 1)
    image_format = "png" if "png" in header.lower() else "jpeg"

    return payload, image_format


def _validate_base64(value: str) -> None:
    base64.b64decode(value, validate=True)


def _load_embedded_ocr_module() -> ModuleType:
    # Lazy import keeps health checks independent of OCR dependencies and the DB.
    from app.services import ocr_engine

    return ocr_engine


class OcrService:
    async def recognize_image(
        self,
        image_base64: str,
        source: str = "unknown",
    ) -> OcrResult:
        payload, image_format = _strip_data_url(image_base64)
        if len(payload) > 16 * 1024 * 1024:
            return OcrResult(status="error", message="Изображение слишком большое. Максимум 12 МБ.")
        logger.info(
            "OCR image received source=%s image_format=%s payload_chars=%s",
            source,
            image_format,
            len(payload),
        )

        try:
            _validate_base64(payload)
        except Exception:
            logger.warning(
                "OCR image rejected source=%s reason=invalid_base64",
                source,
            )
            return OcrResult(
                status="error",
                message="Image payload is not valid base64.",
            )

        if settings.ocr_mode == "embedded":
            return await self._recognize_embedded(
                payload,
                source=source,
            )

        if settings.ocr_mode == "disabled":
            return OcrResult(
                status="ocr_unavailable",
                message="OCR/ML service is disabled.",
            )

        if not settings.ocr_service_url:
            return OcrResult(
                status="ocr_unavailable",
                message="OCR/ML service URL is not configured.",
            )

        request_id = str(uuid.uuid4())
        logger.info(
            "OCR request started mode=http request_id=%s source=%s image_format=%s",
            request_id,
            source,
            image_format,
        )

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{settings.ocr_service_url.rstrip('/')}/recognize",
                    json={
                        "request_id": request_id,
                        "image": payload,
                        "image_format": image_format,
                        "locale": "ru",
                        "context": {
                            "source": source,
                        },
                    },
                )

            response.raise_for_status()
        except httpx.HTTPError as error:
            return OcrResult(
                status="ocr_unavailable",
                message=f"OCR/ML service request failed: {error}",
            )

        data = response.json()
        logger.info(
            "OCR HTTP response received request_id=%s status=%s processing_time_ms=%s",
            request_id,
            data.get("status"),
            data.get("processing_time_ms"),
        )

        if data.get("status") != "success":
            error = data.get("error")
            message = (
                error.get("message")
                if isinstance(error, dict)
                else "OCR/ML service returned an error."
            )

            return OcrResult(
                status="error",
                message=message,
            )

        ocr = data.get("ocr")
        raw_text = (
            ocr.get("raw_text")
            if isinstance(ocr, dict)
            else None
        )
        confidence = (
            ocr.get("ocr_confidence")
            if isinstance(ocr, dict)
            else None
        )
        composition = clean_composition_text(
            data.get("extracted_composition")
        )
        allergens = clean_composition_text(
            data.get("extracted_allergens")
        )

        if composition and not is_probable_composition_text(composition):
            logger.info(
                "OCR composition rejected request_id=%s source=%s reason=not_probable text=%r",
                request_id,
                source,
                composition,
            )
            composition = ""

        processing_time_ms = data.get("processing_time_ms")

        result = OcrResult(
            status="success",
            rawText=clean_raw_ocr_text(raw_text),
            compositionText=composition,
            allergensText=allergens,
            confidence=confidence if isinstance(confidence, (int, float)) else None,
            processingTimeMs=(
                processing_time_ms
                if isinstance(processing_time_ms, int)
                else None
            ),
            message="Composition recognized.",
        )

        self._log_ocr_result(
            "http",
            result,
            request_id=request_id,
            source=source,
        )

        return result

    async def _recognize_embedded(
        self,
        payload: str,
        source: str,
    ) -> OcrResult:
        # The worker owns the lock, even if the HTTP request is cancelled.
        def recognize() -> OcrResult:
            if not _embedded_lock.acquire(blocking=False):
                return OcrResult(status="ocr_unavailable", message="Распознавание занято. Повторите через несколько секунд.")
            try:
                return self._recognize_embedded_sync(payload, source)
            finally:
                _embedded_lock.release()

        try:
            return await asyncio.to_thread(recognize)
        except Exception as error:
            logger.warning("Embedded OCR failed source=%s error_type=%s", source, type(error).__name__)
            return OcrResult(
                status="ocr_unavailable",
                message="Не удалось обработать фотографию. Повторите снимок или попробуйте позже.",
            )

    def _recognize_embedded_sync(
        self,
        payload: str,
        source: str,
    ) -> OcrResult:
        start_time = time.time()
        request_id = str(uuid.uuid4())
        logger.info(
            "OCR request started mode=embedded request_id=%s source=%s",
            request_id,
            source,
        )

        embedded_ocr = _load_embedded_ocr_module()

        image = embedded_ocr.decode_base64_image(payload)
        if image is None:
            return OcrResult(
                status="error",
                message="Embedded OCR could not decode image.",
            )

        gray, metrics = embedded_ocr.adaptive_enhance(image)
        logger.info("OCR preprocessing request_id=%s source=%s metrics=%s", request_id, source, metrics)
        raw_text, confidence = embedded_ocr.run_ocr(gray)

        if not raw_text.strip() or confidence < embedded_ocr.CONFIDENCE_RETAKE_THRESHOLD:
            result = OcrResult(
                status="needs_retake", rawText=clean_raw_ocr_text(raw_text), confidence=confidence,
                processingTimeMs=round((time.time() - start_time) * 1000),
                message="Текст распознан неуверенно. Сфотографируйте состав ближе и при хорошем освещении.",
            )
            self._log_ocr_result("embedded", result, request_id=request_id, source=source)
            return result

        composition = clean_composition_text(
            embedded_ocr.extract_composition_block(raw_text)
        )
        allergens = clean_composition_text(
            embedded_ocr.extract_allergens_block(raw_text)
        )

        result = OcrResult(
            status="success",
            rawText=clean_raw_ocr_text(raw_text),
            compositionText=composition,
            allergensText=allergens,
            confidence=confidence if isinstance(confidence, (int, float)) else None,
            processingTimeMs=round((time.time() - start_time) * 1000),
            message="Composition recognized by embedded OCR.",
        )

        self._log_ocr_result(
            "embedded",
            result,
            request_id=request_id,
            source=source,
        )

        return result

    def _log_ocr_result(
        self,
        mode: str,
        result: OcrResult,
        request_id: str,
        source: str,
    ) -> None:
        logger.info(
            "OCR result mode=%s request_id=%s source=%s status=%s confidence=%s processing_time_ms=%s",
            mode,
            request_id,
            source,
            result.status,
            result.confidence,
            result.processing_time_ms,
        )
        logger.info(
            "OCR raw_text request_id=%s text=%r",
            request_id,
            result.raw_text,
        )
        logger.info(
            "OCR composition_text request_id=%s text=%r",
            request_id,
            result.composition_text,
        )
        logger.info(
            "OCR allergens_text request_id=%s text=%r",
            request_id,
            result.allergens_text,
        )

    async def enqueue_image(self, image_base64: str) -> OcrResult:
        if settings.ocr_mode == "disabled":
            return OcrResult(
                status="ocr_unavailable",
                message="OCR/ML service is not configured yet.",
            )

        return OcrResult(
            status="queued",
            jobId=str(uuid.uuid4()),
            message="OCR job has been queued.",
        )
