"""Personal invitations and multi-label research; ordinary app identity is unchanged."""
import secrets
from typing import Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.identity_dependencies import require_admin, admin_write, guest_user, guest_write, throttle
from app.db.session import get_annotation_session
from app.services.identity import Principal
from app.schemas.research import Payload
from app.services.research_access import ACCESS_SQL, digest, enrollment
from app.services.research_service import ResearchService, encoded, normalize_codes

router = APIRouter()

class EnrollmentInput(Payload):
    id: UUID
    study_id: UUID
    participant_id: UUID | None = None
    case_ids: list[UUID] = Field(min_length=1,max_length=50)
    exclusion_codes: list[str] = Field(default_factory=list,max_length=100)

class CaseInput(Payload):
    title: str = Field(min_length=1,max_length=200)

class ExchangeInput(Payload):
    token: str = Field(min_length=40,max_length=150)

class RatingInput(Payload):
    score: int = Field(ge=0,le=10,strict=True)

@router.get("/admin/research/participants")
async def participants(q: str = Query("",max_length=50), offset: int = Query(0,ge=0),
        admin: Principal=Depends(require_admin), db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        return [dict(r) for r in (await db.execute(text("""SELECT id,participant_number FROM research.participants
            WHERE CAST(participant_number AS text) LIKE :q ORDER BY participant_number DESC LIMIT 50 OFFSET :offset"""),
            dict(q="%"+q.strip().removeprefix("P-").lstrip("0")+"%",offset=offset))).mappings()]

@router.get("/admin/research/cases")
async def cases(q:str=Query("",max_length=200),offset:int=Query(0,ge=0),
        admin:Principal=Depends(require_admin),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        return [dict(r) for r in (await db.execute(text("""SELECT id,title FROM research.cases
            WHERE title ILIKE :q ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset"""),
            dict(q="%"+q+"%",offset=offset))).mappings()]

@router.post("/admin/research/cases")
async def create_case(payload:CaseInput,admin:Principal=Depends(admin_write),db:AsyncSession=Depends(get_annotation_session)):
    if not payload.title.strip():raise HTTPException(422,"Введите название этикетки.")
    async with db.begin():
        id=uuid4()
        await db.execute(text("INSERT INTO research.cases(id,title) VALUES(:id,:title)"),dict(id=id,title=payload.title.strip()))
        await ResearchService(db).audit(admin.user.id,"case_created",details={"case_id":str(id)})
        return {"id":id,"title":payload.title.strip()}

@router.post("/admin/research/enrollments")
async def create_enrollment(payload:EnrollmentInput,admin:Principal=Depends(admin_write),
                            db:AsyncSession=Depends(get_annotation_session)):
    await throttle("research-enrollment:"+str(admin.user.id),60,3600)
    async with db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"),dict(id=str(payload.id)))
        old=(await db.execute(text("SELECT * FROM research.enrollments WHERE id=:id"),dict(id=payload.id))).mappings().first()
        if old:
            old_cases=list((await db.scalars(text("SELECT case_id FROM research.assignments WHERE enrollment_id=:id ORDER BY position"),
                                            dict(id=payload.id))).all())
            codes=normalize_codes(payload.exclusion_codes)
            if (old["study_id"]!=payload.study_id or old_cases!=payload.case_ids
                or (payload.participant_id and old["participant_id"]!=payload.participant_id)
                or sorted(x["e_code"] for x in old["exclusions"])!=codes):
                raise HTTPException(409,"Идентификатор назначения уже использован с другими условиями.")
            return dict(old)
        study=(await db.execute(text("SELECT * FROM research.studies WHERE id=:id FOR SHARE"),dict(id=payload.study_id))).mappings().first()
        if not study:
            raise HTTPException(404,"Исследование не найдено.")
        if len(set(payload.case_ids))!=len(payload.case_ids):
            raise HTTPException(422,"Одна этикетка не должна повторяться в наборе.")
        found=await db.scalar(text("SELECT count(*) FROM research.cases WHERE id=ANY(CAST(:ids AS uuid[]))"),dict(ids=payload.case_ids))
        if found!=len(payload.case_ids):
            raise HTTPException(422,"Некоторые этикетки не найдены.")
        codes=normalize_codes(payload.exclusion_codes)
        exclusions=[dict(r) for r in (await db.execute(text("""SELECT id::text AS ingredient_id,upper(e_code) AS e_code
            FROM catalog.ingredients WHERE is_active AND upper(e_code)=ANY(CAST(:codes AS text[])) ORDER BY e_code"""),
            dict(codes=codes))).mappings()]
        if len(exclusions)!=len(codes) or (study["scenario"]=="comparison" and not codes):
            raise HTTPException(422,"Выберите существующие E-добавки; для сравнения нужна хотя бы одна.")
        participant=payload.participant_id
        if participant:
            if not await db.scalar(text("SELECT id FROM research.participants WHERE id=:id FOR UPDATE"),dict(id=participant)):
                raise HTTPException(404,"Участник не найден.")
        else:
            participant=uuid4()
            await db.execute(text("INSERT INTO research.participants(id) VALUES(:id)"),dict(id=participant))
        if await db.scalar(text("SELECT id FROM research.enrollments WHERE participant_id=:p AND study_id=:s"),
                           dict(p=participant,s=payload.study_id)):
            raise HTTPException(409,"Этот участник уже назначен на исследование. Откройте существующее назначение.")
        await db.execute(text("""INSERT INTO research.enrollments(id,participant_id,study_id,exclusions)
            VALUES(:id,:p,:s,CAST(:x AS jsonb))"""),dict(id=payload.id,p=participant,s=payload.study_id,x=encoded(exclusions)))
        for position,case in enumerate(payload.case_ids,1):
            await db.execute(text("INSERT INTO research.assignments(id,enrollment_id,case_id,position) VALUES(:id,:e,:c,:p)"),
                             dict(id=uuid4(),e=payload.id,c=case,p=position))
        await ResearchService(db).audit(admin.user.id,"enrollment_created",details={"id":str(payload.id),"participant":str(participant)})
        return {"id":payload.id,"participant_id":participant}

@router.get("/admin/research/enrollments")
async def enrollments(offset:int=Query(0,ge=0),admin:Principal=Depends(require_admin),
                      db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        return [dict(r) for r in (await db.execute(text("""SELECT e.*,p.participant_number,s.title,
            rating.score,(SELECT count(*) FROM research.assignments WHERE enrollment_id=e.id) AS task_count,
            (SELECT count(*) FROM research.assignments a JOIN research.runs r ON r.id=a.run_id
                WHERE a.enrollment_id=e.id AND r.stage IN ('completed','aborted')) AS finished_count
            FROM research.enrollments e JOIN research.participants p ON p.id=e.participant_id
            JOIN research.studies s ON s.id=e.study_id LEFT JOIN research.satisfaction rating ON rating.participant_id=p.id
            ORDER BY e.created_at DESC,e.id LIMIT 50 OFFSET :offset"""),dict(offset=offset))).mappings()]

@router.post("/admin/research/enrollments/{id}/invitation")
async def invitation(id:UUID,admin:Principal=Depends(admin_write),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        if not await db.scalar(text("SELECT id FROM research.enrollments WHERE id=:id FOR UPDATE"),dict(id=id)):
            raise HTTPException(404,"Назначение не найдено.")
        await db.execute(text("UPDATE research.invitations SET revoked_at=now() WHERE enrollment_id=:id AND revoked_at IS NULL"),dict(id=id))
        token=secrets.token_urlsafe(32)
        await db.execute(text("INSERT INTO research.invitations(id,enrollment_id,token_hash) VALUES(:i,:e,:t)"),
                         dict(i=uuid4(),e=id,t=digest(token)))
        await ResearchService(db).audit(admin.user.id,"invitation_rotated",details={"enrollment_id":str(id)})
        return {"token":token}

@router.delete("/admin/research/enrollments/{id}/invitation")
async def revoke(id:UUID,admin:Principal=Depends(admin_write),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        await db.execute(text("SELECT id FROM research.enrollments WHERE id=:id FOR UPDATE"),dict(id=id))
        await db.execute(text("UPDATE research.invitations SET revoked_at=now() WHERE enrollment_id=:id AND revoked_at IS NULL"),dict(id=id))
        await ResearchService(db).audit(admin.user.id,"invitation_revoked",details={"enrollment_id":str(id)})
        return {"saved":True}

@router.post("/research/invitations/exchange")
async def exchange(payload:ExchangeInput,user:Principal=Depends(guest_write),db:AsyncSession=Depends(get_annotation_session)):
    await throttle("research-invitation:"+str(user.user.id),30,3600)
    async with db.begin():
        inv=(await db.execute(text("""SELECT * FROM research.invitations WHERE token_hash=:token
            AND revoked_at IS NULL AND expires_at>now() FOR SHARE"""),dict(token=digest(payload.token)))).mappings().first()
        if not inv:
            raise HTTPException(404,"Ссылка недействительна или отозвана. Обратитесь к организатору.")
        await db.execute(text("INSERT INTO research.access_grants(invitation_id,user_id) VALUES(:i,:u) ON CONFLICT DO NOTHING"),
                         dict(i=inv["id"],u=user.user.id))
        return {"enrollment_id":inv["enrollment_id"]}

@router.get("/research/enrollments")
async def my_enrollments(user:Principal=Depends(guest_user),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        return [dict(r) for r in (await db.execute(text("""SELECT e.id,p.participant_number,s.title FROM research.enrollments e
            JOIN research.participants p ON p.id=e.participant_id JOIN research.studies s ON s.id=e.study_id WHERE """+
            ACCESS_SQL+" ORDER BY e.created_at DESC"),dict(user=user.user.id))).mappings()]

@router.get("/research/enrollments/{id}")
async def detail(id:UUID,user:Principal=Depends(guest_user),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        e=await enrollment(db,id,user.user.id)
        e["tasks"]=[dict(r) for r in (await db.execute(text("""SELECT a.*,c.title,r.stage FROM research.assignments a
            JOIN research.cases c ON c.id=a.case_id LEFT JOIN research.runs r ON r.id=a.run_id
            WHERE a.enrollment_id=:id ORDER BY position"""),dict(id=id))).mappings()]
        e["rating"]=await db.scalar(text("SELECT score FROM research.satisfaction WHERE participant_id=:id"),dict(id=e["participant_id"]))
        e["rating_available"]=bool(e["tasks"]) and all(t["stage"] in ("completed","aborted") for t in e["tasks"]) and any(t["stage"]=="completed" for t in e["tasks"])
        return e

@router.post("/research/enrollments/{id}/rating")
async def rate(id:UUID,payload:RatingInput,user:Principal=Depends(guest_write),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        e=await enrollment(db,id,user.user.id)
        await db.execute(text("SELECT id FROM research.participants WHERE id=:id FOR UPDATE"),dict(id=e["participant_id"]))
        stages=list((await db.scalars(text("""SELECT r.stage FROM research.assignments a LEFT JOIN research.runs r ON r.id=a.run_id
            WHERE a.enrollment_id=:id"""),dict(id=id))).all())
        if not stages or any(s not in ("completed","aborted") for s in stages) or "completed" not in stages:
            raise HTTPException(409,"Оценка доступна после всех заданий, если хотя бы одно завершено.")
        old=await db.scalar(text("SELECT score FROM research.satisfaction WHERE participant_id=:id"),dict(id=e["participant_id"]))
        if old is not None:
            if old!=payload.score:
                raise HTTPException(409,"Оценка участника уже сохранена.")
            return {"saved":True}
        await db.execute(text("INSERT INTO research.satisfaction(participant_id,enrollment_id,score) VALUES(:p,:e,:s)"),
                         dict(p=e["participant_id"],e=id,s=payload.score))
        return {"saved":True}

@router.get("/admin/research/enrollments/{id}")
async def admin_detail(id:UUID,admin:Principal=Depends(require_admin),db:AsyncSession=Depends(get_annotation_session)):
    async with db.begin():
        row=(await db.execute(text("SELECT * FROM research.enrollments WHERE id=:id"),dict(id=id))).mappings().first()
        if not row:raise HTTPException(404,"Назначение не найдено.")
        return {"exclusions":row["exclusions"],"tasks":[dict(r) for r in (await db.execute(text("""
            SELECT a.id,a.position,a.run_id,c.title,r.stage FROM research.assignments a
            JOIN research.cases c ON c.id=a.case_id LEFT JOIN research.runs r ON r.id=a.run_id
            WHERE a.enrollment_id=:id ORDER BY position"""),dict(id=id))).mappings()]}
