import base64
import asyncio
import importlib.util
import time
import uuid
from pathlib import Path
from types import ModuleType

import httpx

from app.core.config import settings
from app.schemas.ocr import OcrResult
from app.services.composition_cleaner import clean_composition_text, clean_raw_ocr_text


def _strip_data_url(image_base64: str) -> tuple[str, str]:
    if "," not in image_base64 or not image_base64.startswith("data:"):
        return image_base64, "jpeg"

    header, payload = image_base64.split(",", 1)
    image_format = "png" if "png" in header.lower() else "jpeg"

    return payload, image_format


def _validate_base64(value: str) -> None:
    base64.b64decode(value, validate=True)


def _load_embedded_ocr_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[3] / "ocr_service.py"

    if not module_path.exists():
        raise RuntimeError("Embedded OCR module ocr_service.py was not found.")

    spec = importlib.util.spec_from_file_location(
        "toxicheck_embedded_ocr_service",
        module_path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Embedded OCR module could not be loaded.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


class OcrService:
    async def recognize_image(self, image_base64: str) -> OcrResult:
        payload, image_format = _strip_data_url(image_base64)

        try:
            _validate_base64(payload)
        except Exception:
            return OcrResult(
                status="error",
                message="Image payload is not valid base64.",
            )

        if settings.ocr_mode == "embedded":
            return await self._recognize_embedded(payload)

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
                            "source": "toxicheck-composition-scan",
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

        processing_time_ms = data.get("processing_time_ms")

        return OcrResult(
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

    async def _recognize_embedded(self, payload: str) -> OcrResult:
        try:
            return await asyncio.to_thread(
                self._recognize_embedded_sync,
                payload,
            )
        except Exception as error:
            return OcrResult(
                status="ocr_unavailable",
                message=f"Embedded OCR failed: {error}",
            )

    def _recognize_embedded_sync(self, payload: str) -> OcrResult:
        start_time = time.time()
        embedded_ocr = _load_embedded_ocr_module()

        image = embedded_ocr.decode_base64_image(payload)
        if image is None:
            return OcrResult(
                status="error",
                message="Embedded OCR could not decode image.",
            )

        resized = embedded_ocr.resize_if_needed(image)
        gray = embedded_ocr.cv2.cvtColor(
            resized,
            embedded_ocr.cv2.COLOR_BGR2GRAY,
        )
        raw_text, confidence = embedded_ocr.run_ocr(gray)

        composition = clean_composition_text(
            embedded_ocr.extract_composition_block(raw_text)
        )
        allergens = clean_composition_text(
            embedded_ocr.extract_allergens_block(raw_text)
        )

        return OcrResult(
            status="success",
            rawText=clean_raw_ocr_text(raw_text),
            compositionText=composition,
            allergensText=allergens,
            confidence=confidence if isinstance(confidence, (int, float)) else None,
            processingTimeMs=round((time.time() - start_time) * 1000),
            message="Composition recognized by embedded OCR.",
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
