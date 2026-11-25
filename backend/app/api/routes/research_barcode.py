"""Barcode is measured after Manual/OCR and never overwrites their answers."""
import asyncio
import time
from typing import Literal
from uuid import UUID
import httpx
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.identity_dependencies import guest_write,throttle
from app.db.session import get_annotation_session
from app.schemas.research import Payload,DisplayInput
from app.schemas.analysis import AnalysisPreferences
from app.services.identity import Principal
from app.services.research_service import ResearchService,encoded
from app.services.scan_service import ScanService

router=APIRouter(prefix="/research/runs/{run_id}/barcode")

class Start(Payload):
    id:UUID

class Lookup(Payload):
    barcode:str=Field(pattern=r"^\d{8,14}$")

class Finish(Payload):
    outcome:Literal["no_code","camera_denied","camera_error","interrupted","timeout"]

async def owned_attempt(db,run_id,id,user):
    run=await ResearchService(db).owned(run_id,user,lock=True)
    row=(await db.execute(text("SELECT * FROM research.barcode_attempts WHERE id=:id AND run_id=:run FOR UPDATE"),
                          dict(id=id,run=run_id))).mappings().first()
    if not row:raise HTTPException(404,"Попытка не найдена.")
    return run,dict(row)

@router.post("/attempts")
async def start(run_id:UUID,payload:Start,user:Principal=Depends(guest_write),db:AsyncSession=Depends(get_annotation_session)):
    await throttle("research-barcode:"+str(user.user.id),60,3600)
    async with db.begin():
        run=await ResearchService(db).owned(run_id,user.user.id,lock=True)
        old=(await db.execute(text("SELECT * FROM research.barcode_attempts WHERE id=:id"),dict(id=payload.id))).mappings().first()
        if old:
            if old["run_id"]!=run_id:raise HTTPException(409,"Идентификатор занят.")
            return dict(old)
        if run["stage"]!="barcode_active":raise HTTPException(409,"Начните этап штрихкода.")
        active=await db.scalar(text("SELECT id FROM research.barcode_attempts WHERE run_id=:id AND status IN ('scanning','lookup')"),dict(id=run_id))
        if active:raise HTTPException(409,"Предыдущая попытка ещё активна. Восстановите её перед повтором.")
        if await db.scalar(text("SELECT count(*) FROM research.barcode_attempts WHERE run_id=:id"),dict(id=run_id))>=20:
            raise HTTPException(422,"Достигнут лимит 20 попыток.")
        return dict((await db.execute(text("INSERT INTO research.barcode_attempts(id,run_id) VALUES(:id,:r) RETURNING *"),
                        dict(id=payload.id,r=run_id))).mappings().one())

@router.post("/attempts/{id}/lookup")
async def lookup(run_id:UUID,id:UUID,payload:Lookup,user:Principal=Depends(guest_write),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        run,row=await owned_attempt(db,run_id,id,user.user.id)
        if row["status"]!="scanning":
            if row["barcode"] and row["barcode"]!=payload.barcode:raise HTTPException(409,"Попытка уже содержит другой код.")
            return row
        if run["stage"]!="barcode_active":raise HTTPException(409,"Этап закрыт.")
        await db.execute(text("""UPDATE research.barcode_attempts SET status='lookup',barcode=:b,detected_at=now(),
            detection_ms=GREATEST(0,round(extract(epoch FROM (now()-started_at))*1000)) WHERE id=:id"""),
            dict(id=id,b=payload.barcode))
    start_time=time.perf_counter()
    status,error,result="service_error",None,None
    try:
        # Bounded server lookup to a configured OFF endpoint; no client-controlled URL.
        value=await asyncio.wait_for(ScanService(db).scan_barcode(payload.barcode,
            AnalysisPreferences(excludedIngredientIds=[x["ingredient_id"] for x in run["exclusions"]])),timeout=15)
        result=value.model_dump(mode="json",by_alias=True)
        status="product_not_found" if not value.product_found else "success" if value.ingredients_text else "composition_missing"
    except (asyncio.TimeoutError,httpx.TimeoutException):
        status,error="timeout","Сервис не ответил вовремя."
    except httpx.RequestError:
        status,error="network_error","Не удалось связаться с сервисом."
    except Exception:
        status,error="service_error","Ошибка получения или анализа сведений о товаре."
    finally:
        # ScanService's read queries may have started an implicit transaction.
        if db.in_transaction():await db.rollback()
    elapsed=round((time.perf_counter()-start_time)*1000)
    async with db.begin():
        await ResearchService(db).owned(run_id,user.user.id,lock=True)
        # A late result cannot undo an interruption or a closed research stage.
        await db.execute(text("""UPDATE research.barcode_attempts SET status=:s,error=:e,result=CAST(:r AS jsonb),
            lookup_ms=:ms,finished_at=now(),total_ms=GREATEST(0,round(extract(epoch FROM (now()-started_at))*1000))
            WHERE id=:id AND status='lookup'"""),dict(id=id,s=status,e=error,r=encoded(result),ms=elapsed))
        return dict((await db.execute(text("SELECT * FROM research.barcode_attempts WHERE id=:id"),dict(id=id))).mappings().one())

@router.post("/attempts/{id}/finish")
async def finish(run_id:UUID,id:UUID,payload:Finish,user:Principal=Depends(guest_write),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        run,row=await owned_attempt(db,run_id,id,user.user.id)
        if row["status"] not in ("scanning","lookup"):return row
        if payload.outcome!="interrupted" and row["status"]=="lookup":
            raise HTTPException(409,"Запрос выполняется. Можно отметить прерывание.")
        await db.execute(text("""UPDATE research.barcode_attempts SET status=:status,finished_at=now(),
            total_ms=GREATEST(0,round(extract(epoch FROM (now()-started_at))*1000)) WHERE id=:id"""),
            dict(id=id,status=payload.outcome))
        await ResearchService(db).audit(user.user.id,"barcode_"+payload.outcome,run_id,{"attempt_id":str(id)})
        return dict((await db.execute(text("SELECT * FROM research.barcode_attempts WHERE id=:id"),dict(id=id))).mappings().one())

@router.post("/attempts/{id}/displayed")
async def displayed(run_id:UUID,id:UUID,payload:DisplayInput,user:Principal=Depends(guest_write),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        _,row=await owned_attempt(db,run_id,id,user.user.id)
        if row["status"] in ("scanning","lookup"):raise HTTPException(409,"Результат ещё не готов.")
        if row["client_e2e_ms"] is not None:
            if row["client_e2e_ms"]!=payload.app_e2e_ms:raise HTTPException(409,"Замер уже сохранён.")
            return {"saved":True}
        if row["total_ms"] is not None and payload.app_e2e_ms+1000<row["total_ms"]:
            raise HTTPException(422,"Замер меньше серверного времени.")
        await db.execute(text("UPDATE research.barcode_attempts SET displayed_at=now(),client_e2e_ms=:ms WHERE id=:id"),
                         dict(id=id,ms=payload.app_e2e_ms))
        return {"saved":True}
