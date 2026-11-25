"""Admin verification is the only path from OCR guesses to trusted training labels."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.identity_dependencies import require_admin, admin_write, throttle
from app.db.session import get_annotation_session
from app.services.identity import Principal

router=APIRouter(dependencies=[Depends(require_admin)])

class LabelInput(BaseModel):
    is_correct: bool = Field(alias="isCorrect")
    corrected_ingredient_id: UUID | None = Field(default=None, alias="correctedIngredientId")
    @model_validator(mode="after")
    def consistent(self):
        if self.is_correct and self.corrected_ingredient_id:
            raise ValueError("Для подтверждения исходного варианта исправление не требуется.")
        return self

@router.get("/reviews")
async def pending(limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),
                  actor:Principal=Depends(require_admin),db:AsyncSession=Depends(get_annotation_session)):
    await throttle("ocr:reviews:"+str(actor.user.id),120,60)
    total=await db.scalar(text("SELECT count(*) FROM decision_tree.review_queue WHERE status='pending'"))
    rows=(await db.execute(text("""SELECT * FROM decision_tree.review_queue WHERE status='pending'
        ORDER BY is_hazard DESC,created_at,review_id LIMIT :limit OFFSET :offset"""),
        dict(limit=limit,offset=offset))).mappings().all()
    return dict(items=[dict(r) for r in rows],total=total)

@router.put("/reviews/{review_id}")
async def label(review_id:UUID,payload:LabelInput,actor:Principal=Depends(admin_write),
                db:AsyncSession=Depends(get_annotation_session)):
    await throttle("ocr:label:"+str(actor.user.id),120,60)
    async with db.begin():
        row=(await db.execute(text("SELECT * FROM decision_tree.review_queue WHERE review_id=:id FOR UPDATE"),
                              dict(id=review_id))).mappings().first()
        if not row:raise HTTPException(404,"Запись не найдена.")
        corrected=None
        if payload.corrected_ingredient_id:
            corrected=await db.scalar(text("SELECT canonical_name_ru FROM catalog.ingredients WHERE id=:id AND is_active"),
                                      dict(id=payload.corrected_ingredient_id))
            if not corrected:raise HTTPException(422,"Выберите действующий ингредиент из справочника.")
        decision="correct" if payload.is_correct else "incorrect"
        if row["status"]!="pending":
            if row["label"]==decision and row["corrected_canonical_name"]==corrected:return {"saved":True}
            raise HTTPException(409,"Запись уже размечена иначе. Обновите очередь.")
        # User corrections may propose arbitrary text. Never teach an unknown canonical label.
        if payload.is_correct and not await db.scalar(text("""SELECT EXISTS(SELECT 1 FROM catalog.ingredients
                WHERE is_active AND canonical_name_ru=:name)"""),dict(name=row["candidate_canonical_name"])):
            raise HTTPException(422,"Исходного названия нет в справочнике. Отклоните вариант и выберите правильный ингредиент.")
        await db.execute(text("""UPDATE decision_tree.review_queue
            SET status='labeled',label=:label,corrected_canonical_name=:corrected,labeled_at=now()
            WHERE review_id=:id"""),dict(id=review_id,label=decision,corrected=corrected))
        examples=[(row["candidate_canonical_name"],int(payload.is_correct))]
        if corrected:examples.append((corrected,1))
        for name,value in examples:
            await db.execute(text("""INSERT INTO decision_tree.training_data(token_raw,canonical_name,label,source)
                VALUES(:raw,:name,:label,:source)"""),
                dict(raw=row["token_raw"],name=name,label=value,source="admin_review:"+str(review_id)+":"+str(actor.user.id)))
    return {"saved":True}
