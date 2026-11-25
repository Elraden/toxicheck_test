"""Assignment access is invitation-scoped; browser users are transport identities."""
import hashlib
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import text
from app.services.research_service import encoded

ACCESS_SQL = """EXISTS (
 SELECT 1 FROM research.invitations inv JOIN research.access_grants g ON g.invitation_id=inv.id
 WHERE inv.enrollment_id=e.id AND g.user_id=:user AND inv.revoked_at IS NULL AND inv.expires_at>now()
)"""

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

async def enrollment(db, enrollment_id, user_id, lock=False):
    row = (await db.execute(text("""SELECT e.*,p.participant_number,s.title AS study_title,s.scenario
        FROM research.enrollments e JOIN research.participants p ON p.id=e.participant_id
        JOIN research.studies s ON s.id=e.study_id WHERE e.id=:id AND """ + ACCESS_SQL +
        (" FOR UPDATE OF e" if lock else "")), dict(id=enrollment_id,user=user_id))).mappings().first()
    if not row:
        raise HTTPException(404,"Назначение недоступно. Откройте действующую персональную ссылку.")
    return dict(row)

async def assignment_run(db, user_id, payload):
    assignment = (await db.execute(text("SELECT * FROM research.assignments WHERE id=:id"),
                                   dict(id=payload.assignment_id))).mappings().first()
    if not assignment:
        raise HTTPException(404,"Задание не найдено.")
    e = await enrollment(db,assignment["enrollment_id"],user_id,lock=True)
    if e["study_id"] != payload.study_id:
        raise HTTPException(409,"Задание относится к другому исследованию.")
    # Re-read after the enrollment lock to make double clicks and concurrent devices idempotent.
    assignment = (await db.execute(text("SELECT * FROM research.assignments WHERE id=:id"),
                                   dict(id=payload.assignment_id))).mappings().one()
    if assignment["run_id"]:
        return assignment["run_id"]
    if await db.scalar(text("""SELECT EXISTS(SELECT 1 FROM research.assignments a
        LEFT JOIN research.runs r ON r.id=a.run_id
        WHERE a.enrollment_id=:id AND a.position<:position
          AND (r.id IS NULL OR r.stage NOT IN ('completed','aborted')))"""),
        dict(id=e["id"],position=assignment["position"])):
        raise HTTPException(409,"Сначала завершите предыдущее задание.")
    study = (await db.execute(text("SELECT * FROM research.studies WHERE id=:id FOR SHARE"),
                              dict(id=e["study_id"]))).mappings().one()
    if not study["is_open"]:
        raise HTTPException(409,"Исследование закрыто для новых заданий.")
    run_id = uuid4()  # Server-owned identity; assignment guarantees retry idempotence.
    await db.execute(text("""INSERT INTO research.runs
        (id,study_id,participant_id,case_id,scenario,protocol_version,instruction,consent_version,exclusions,stage,barcode_required)
        VALUES(:id,:study,:participant,:case,:scenario,:version,:instruction,'research-consent-v2',
        CAST(:exclusions AS jsonb),:stage,:barcode)"""),dict(id=run_id,study=e["study_id"],
        participant=e["participant_id"],case=assignment["case_id"],scenario=study["scenario"],
        version=study["protocol_version"],instruction=study["instruction"],exclusions=encoded(e["exclusions"]),
        stage="manual_ready" if study["scenario"]=="comparison" else "app_ready",
        barcode=study["scenario"]=="comparison"))
    await db.execute(text("INSERT INTO research.validations(run_id) VALUES(:id)"),dict(id=run_id))
    await db.execute(text("UPDATE research.assignments SET run_id=:run WHERE id=:id"),
                     dict(run=run_id,id=assignment["id"]))
    return run_id
