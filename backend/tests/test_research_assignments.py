"""New research protocol exercised against disposable PostgreSQL only."""
import asyncio
import os
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock,patch

from fastapi import HTTPException
from app.api.identity_dependencies import guest_user,guest_write
from app.schemas.research import StageInput
from app.schemas.scan import BarcodeScanResponse
from app.services.research_service import transition
from app.services.research_export import sheets
from app.db.initialize import SCHEMAS,DB_DIR
import test_ocr_ml_update as ocr_fixture

class AssignmentRulesTests(unittest.TestCase):
    def test_barcode_is_after_ocr_and_legacy_stays_unchanged(self):
        now=datetime.now(timezone.utc)
        run=dict(revision=0,stage="app_active",exclusions=[],app_started_at=now-timedelta(seconds=5))
        payload=StageInput(revision=0,action="finish_app")
        self.assertEqual(transition(run,payload,now)["stage"],"labeling")
        self.assertEqual(transition({**run,"barcode_required":True},payload,now)["stage"],"barcode_ready")
        with self.assertRaises(HTTPException):
            transition({**run,"stage":"manual_active","barcode_required":True},StageInput(revision=0,action="start_barcode"),now)
        changes=transition({**run,"stage":"barcode_active","barcode_started_at":now-timedelta(seconds=6)},
                           StageInput(revision=0,action="finish_barcode"),now)
        self.assertEqual(changes["barcode_duration_ms"],6000)
        self.assertEqual(changes["stage"],"labeling")

    def test_export_does_not_multiply_participant_rating_by_labels(self):
        base=dict(id="r1",participant_id="p",participant_number=127,photos=[],attempts=[],audit=[],validation=None,
                  satisfaction=dict(participant_id="p",score=0),barcode_attempts=[dict(id="b",status="composition_missing")])
        result=sheets([base,{**base,"id":"r2"}])
        self.assertEqual(len(result["satisfaction"]),1)
        self.assertEqual(result["satisfaction"][0]["score"],0)
        self.assertEqual(len(result["barcode_attempts"]),2)
        self.assertNotIn("satisfaction",result["runs"][0])

@unittest.skipUnless(os.environ.get("TOXICHECK_BOOTSTRAP_TEST")=="1","Disposable local PostgreSQL")
class AssignmentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup_database=ocr_fixture.OcrDatabaseTests.cleanup_database

    async def asyncSetUp(self):
        await ocr_fixture.OcrDatabaseTests.asyncSetUp(self)
        admin_id=await self.conn.fetchval("INSERT INTO public.users(role) VALUES('admin') RETURNING id")
        self.actor.user.id=admin_id
        self.users=[await self.conn.fetchval("INSERT INTO public.users DEFAULT VALUES RETURNING id") for _ in range(3)]
        self.guest=SimpleNamespace(user=SimpleNamespace(id=self.users[0]),csrf="test")
        self.app.dependency_overrides[guest_user]=lambda:self.guest
        self.app.dependency_overrides[guest_write]=lambda:self.guest
        for path in ("app.api.routes.research_assignments.throttle","app.api.routes.research_barcode.throttle","app.api.routes.research.throttle"):
            p=patch(path,AsyncMock());p.start();self.addCleanup(p.stop)
        self.study=uuid4()
        await self.conn.execute("""INSERT INTO research.studies(id,title,instruction,scenario,protocol_version,is_open)
            VALUES($1,'Study','Read label','comparison','2',true)""",self.study)
        self.cases=[uuid4(),uuid4()]
        for i,id in enumerate(self.cases):
            await self.conn.execute("INSERT INTO research.cases(id,title) VALUES($1,$2)",id,"Label "+str(i+1))
        self.eid=str(uuid4())
        self.payload=dict(id=self.eid,study_id=str(self.study),case_ids=list(map(str,self.cases)),exclusion_codes=["E216"])
        r=await self.client.post("/api/admin/research/enrollments",json=self.payload)
        self.assertEqual(r.status_code,200,r.text)
        self.pid=r.json()["participant_id"]
        self.invite=(await self.client.post("/api/admin/research/enrollments/"+self.eid+"/invitation",json={})).json()["token"]
        await self.exchange()

    async def exchange(self):
        r=await self.client.post("/api/research/invitations/exchange",json={"token":self.invite})
        self.assertEqual(r.status_code,200,r.text)
        self.detail=(await self.client.get("/api/research/enrollments/"+self.eid)).json()

    async def start(self,index=0):
        return await self.client.post("/api/research/runs",json=dict(id=str(uuid4()),study_id=str(self.study),
            assignment_id=self.detail["tasks"][index]["id"],title="Client cannot change assigned label",consent=True))

    async def test_multi_device_identity_order_idempotence_and_revocation(self):
        self.assertEqual(len(self.detail["tasks"]),2)
        self.assertIsInstance(self.detail["participant_number"],int)
        self.assertEqual((await self.start(1)).status_code,409)
        r=await self.start();self.assertEqual(r.status_code,200,r.text)
        run=r.json();self.assertEqual(run["case_id"],str(self.cases[0]))
        again=await self.start();self.assertEqual(again.json()["id"],run["id"])
        self.guest.user.id=self.users[1]
        self.assertEqual((await self.client.get("/api/research/runs/"+run["id"])).status_code,404)
        await self.exchange()
        other=(await self.client.get("/api/research/runs/"+run["id"])).json()
        self.assertEqual(other["participant_id"],self.pid)
        self.assertEqual(other["participant_number"],self.detail["participant_number"])
        r=await self.client.post("/api/admin/research/enrollments",json=self.payload)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM research.participants"),1)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM research.assignments"),2)
        await self.client.delete("/api/admin/research/enrollments/"+self.eid+"/invitation")
        self.assertEqual((await self.client.get("/api/research/runs/"+run["id"])).status_code,404)
        self.assertEqual((await self.client.post("/api/research/invitations/exchange",json={"token":self.invite})).status_code,404)

    async def test_satisfaction_after_tasks_once_globally_and_zero_is_a_real_rating(self):
        path="/api/research/enrollments/"+self.eid+"/rating"
        self.assertEqual((await self.client.post(path,json={"score":8})).status_code,409)
        for index in (0,1):
            r=await self.start(index);self.assertEqual(r.status_code,200,r.text)
            await self.conn.execute("UPDATE research.runs SET stage='completed' WHERE id=$1",UUID(r.json()["id"]))
        for score,status in ((0,200),(0,200),(10,409),(-1,422),(11,422),(2.5,422)):
            r=await self.client.post(path,json={"score":score});self.assertEqual(r.status_code,status,r.text)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM research.satisfaction"),1)
        # The same participant in another study retains the same number and one rating.
        study=uuid4()
        await self.conn.execute("INSERT INTO research.studies(id,title,instruction,scenario,is_open) VALUES($1,'Other','Read','ocr_quality',true)",study)
        r=await self.client.post("/api/admin/research/enrollments",json={**self.payload,"id":str(uuid4()),"study_id":str(study),"participant_id":self.pid})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM research.participants"),1)

    async def test_barcode_stages_outcomes_retries_and_explicit_interruption(self):
        run=(await self.start()).json();runid=run["id"]
        path="/api/research/runs/"+runid
        attempt=str(uuid4())
        self.assertEqual((await self.client.post(path+"/barcode/attempts",json={"id":attempt})).status_code,409)
        # Simulate completed Manual/OCR measurements; API transitions still guard the new stage.
        await self.conn.execute("UPDATE research.runs SET stage='barcode_ready' WHERE id=$1",UUID(runid))
        r=await self.client.post(path+"/stage",json={"revision":0,"action":"start_barcode"})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((await self.client.post(path+"/stage",json={"revision":1,"action":"finish_barcode"})).status_code,409)
        for response,outcome in (
            (BarcodeScanResponse(barcode="12345678",productFound=False),"product_not_found"),
            (BarcodeScanResponse(barcode="12345678",productFound=True),"composition_missing"),
            (BarcodeScanResponse(barcode="12345678",productFound=True,ingredientsText="вода"),"success"),
        ):
            id=str(uuid4());r=await self.client.post(path+"/barcode/attempts",json={"id":id})
            self.assertEqual(r.status_code,200,r.text)
            url=path+"/barcode/attempts/"+id+"/lookup"
            with patch("app.api.routes.research_barcode.ScanService.scan_barcode",AsyncMock(return_value=response)) as lookup:
                for _ in range(2):
                    r=await self.client.post(url,json={"barcode":"12345678"})
                    self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()["status"],outcome)
                self.assertEqual(lookup.await_count,1)
            self.assertIsNotNone(r.json()["lookup_ms"])
        id=str(uuid4());await self.client.post(path+"/barcode/attempts",json={"id":id})
        await self.client.post(path+"/barcode/attempts/"+id+"/finish",json={"outcome":"interrupted"})
        r=await self.client.post(path+"/stage",json={"revision":1,"action":"finish_barcode"})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()["stage"],"labeling")
        self.assertIsNotNone(r.json()["barcode_duration_ms"])
        self.assertEqual(len(r.json()["barcode_attempts"]),4)
        self.assertIsNone(r.json()["manual_duration_ms"]) # Barcode must not fabricate earlier measurements.

    async def test_migration_repeat_keeps_data_and_rejects_unassigned_creation(self):
        run=(await self.start()).json()
        for name in SCHEMAS:
            await self.conn.execute((DB_DIR/name).read_text(encoding="utf-8"))
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM research.assignments"),2)
        self.assertEqual(str(await self.conn.fetchval("SELECT participant_id FROM research.runs WHERE id=$1",UUID(run["id"]))),self.pid)
        self.assertEqual((await self.client.post("/api/research/runs",json=dict(id=str(uuid4()),study_id=str(self.study),title="Unassigned",consent=True))).status_code,422)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM catalog.ingredients"),4)

    async def test_late_barcode_result_cannot_undo_interruption_and_errors_are_distinct(self):
        import httpx
        run=(await self.start()).json();rid=run["id"];path="/api/research/runs/"+rid+"/barcode/attempts"
        await self.conn.execute("UPDATE research.runs SET stage='barcode_active',barcode_started_at=now() WHERE id=$1",UUID(rid))
        bid=str(uuid4());await self.client.post(path,json={"id":bid})
        started,release=asyncio.Event(),asyncio.Event()
        async def delayed(*args,**kwargs):
            started.set();await release.wait()
            return BarcodeScanResponse(barcode="12345678",productFound=True,ingredientsText="вода")
        with patch("app.api.routes.research_barcode.ScanService.scan_barcode",delayed):
            pending=asyncio.create_task(self.client.post(path+"/"+bid+"/lookup",json={"barcode":"12345678"}))
            await asyncio.wait_for(started.wait(),3)
            duplicate=await self.client.post(path+"/"+bid+"/lookup",json={"barcode":"12345678"})
            self.assertEqual(duplicate.json()["status"],"lookup")
            await self.client.post(path+"/"+bid+"/finish",json={"outcome":"interrupted"})
            release.set()
            response=await pending
            self.assertEqual(response.json()["status"],"interrupted")
            self.assertIsNone(response.json()["result"])
        for error,status in ((httpx.ConnectError("private"),"network_error"),(httpx.ReadTimeout("private"),"timeout"),(ValueError("private"),"service_error")):
            bid=str(uuid4());await self.client.post(path,json={"id":bid})
            with patch("app.api.routes.research_barcode.ScanService.scan_barcode",AsyncMock(side_effect=error)):
                response=await self.client.post(path+"/"+bid+"/lookup",json={"barcode":"12345678"})
            self.assertEqual(response.json()["status"],status)
            self.assertNotIn("private",response.text)
