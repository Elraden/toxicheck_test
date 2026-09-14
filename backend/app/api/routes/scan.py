from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.scan import (
    BarcodeScanRequest,
    BarcodeScanResponse,
    CompositionScanRequest,
    CompositionScanResponse,
    ScanJobResponse,
)
from app.services.ocr_service import OcrService
from app.services.scan_service import ScanService

router = APIRouter()


@router.post("/barcode", response_model=BarcodeScanResponse)
async def scan_barcode(
    payload: BarcodeScanRequest,
    session: AsyncSession = Depends(get_db_session),
) -> BarcodeScanResponse:
    service = ScanService(session)

    return await service.scan_barcode(
        barcode=payload.barcode,
        preferences=payload.preferences,
    )


@router.post("/composition", response_model=CompositionScanResponse)
async def scan_composition(
    payload: CompositionScanRequest,
) -> CompositionScanResponse:
    ocr = OcrService()
    result = await ocr.recognize_image(
        payload.image_base64,
        source=payload.capture_source,
    )

    return CompositionScanResponse(
        jobId=result.job_id,
        status=result.status,
        captureSource=payload.capture_source,
        message=result.message or "OCR request completed.",
        recognizedText=result.raw_text,
        ingredientsText=result.composition_text,
        allergensText=result.allergens_text,
        confidence=result.confidence,
        processingTimeMs=result.processing_time_ms,
    )


@router.get("/jobs/{job_id}", response_model=ScanJobResponse)
async def get_scan_job(job_id: str) -> ScanJobResponse:
    return ScanJobResponse(
        jobId=job_id,
        status="not_found",
        message="Persistent OCR jobs storage is not connected yet.",
    )
