import hashlib
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.corrections import SaveCorrectionsRequest, SaveCorrectionsResponse
from app.services.analysis_service import AnalysisService
from app.services.normalization import split_ingredients_text


def apply_corrections(payload: SaveCorrectionsRequest) -> str:
    tokens = split_ingredients_text(payload.ingredients_text) or [""]
    for item in payload.corrections:
        if item.token_index >= len(tokens) or tokens[item.token_index] != item.token_raw:
            raise HTTPException(422, "Состав изменился. Обновите список исправлений.")
        tokens[item.token_index] = item.corrected_text
    return ", ".join(tokens)


class CorrectionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, payload: SaveCorrectionsRequest, user_id: UUID | None = None) -> SaveCorrectionsResponse:
        corrected = apply_corrections(payload)
        fingerprint = hashlib.sha256(payload.ingredients_text.encode()).hexdigest()
        async with self.session.begin():
            analysis = await AnalysisService(self.session).analyze(corrected, payload.preferences)
            for item in payload.corrections:
                params = {
                    "id": item.id, "request_id": payload.request_id,
                    "token_index": item.token_index, "token_raw": item.token_raw,
                    "corrected_text": item.corrected_text, "fingerprint": fingerprint,
                    "user_id": user_id,
                }
                inserted = await self.session.scalar(text("""
                    INSERT INTO decision_tree.user_corrections
                        (id, request_id, token_index, token_raw, corrected_text, input_sha256, user_id)
                    VALUES (:id, :request_id, :token_index, :token_raw, :corrected_text, :fingerprint, :user_id)
                    ON CONFLICT (id) DO NOTHING RETURNING id
                """), params)
                if inserted is None:
                    previous = (await self.session.execute(text("""
                        SELECT request_id, token_index, token_raw, corrected_text, input_sha256, user_id
                        FROM decision_tree.user_corrections WHERE id = :id
                    """), {"id": item.id})).mappings().one()
                    if (str(previous.get("user_id")) != str(user_id)
                            or str(previous["request_id"]) != str(payload.request_id)
                            or previous["token_index"] != item.token_index
                            or previous["token_raw"] != item.token_raw
                            or previous["corrected_text"] != item.corrected_text
                            or previous["input_sha256"] != fingerprint):
                        raise HTTPException(409, "Это исправление уже сохранено с другим текстом.")
                    continue
                # Anonymous feedback is a review candidate, never a trusted training label.
                await self.session.execute(text("""
                    INSERT INTO decision_tree.review_queue
                        (review_id, request_id, token_raw, candidate_canonical_name,
                         match_score, was_accepted, is_hazard)
                    VALUES (:id, :request_id, :token_raw, :corrected_text, 0, false, false)
                """), {**params, "request_id": str(payload.request_id)})
        return SaveCorrectionsResponse(saved=len(payload.corrections),
                                       ingredientsText=corrected, analysis=analysis)
