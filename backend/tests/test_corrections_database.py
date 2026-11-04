"""Opt-in annotation SQL check; every test row is rolled back."""

import os
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.schemas.corrections import SaveCorrectionsRequest
from app.services.correction_service import CorrectionService


@unittest.skipUnless(os.environ.get("TOXICHECK_CORRECTIONS_DB_SMOKE") == "1", "Opt-in rollback-only database check")
class CorrectionsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_annotation_retry_and_reanalysis(self):
        self.assertEqual(make_url(settings.catalog_url).host, os.environ.get("TOXICHECK_DB_EXPECTED_HOST"))
        engine = create_async_engine(settings.catalog_url, connect_args={"timeout": 15, "command_timeout": 30})
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    baseline = await connection.scalar(text("SELECT count(*) FROM decision_tree.training_data"))
                    data = SaveCorrectionsRequest(requestId=uuid4(), ingredientsText="водда, E202", corrections=[{
                        "id": uuid4(), "tokenIndex": 0, "tokenRaw": "водда", "correctedText": "вода",
                    }])
                    identifier = data.corrections[0].id
                    async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint") as session:
                        service = CorrectionService(session)
                        result = await service.save(data)
                        self.assertEqual(result.analysis.unmatched, [])
                        self.assertEqual(len(result.analysis.matched), 2)
                        self.assertEqual(result.analysis.tokens, ["вода", "E202"])
                        await service.save(data)
                        self.assertEqual(await connection.scalar(text(
                            "SELECT count(*) FROM decision_tree.review_queue WHERE review_id=:id"), {"id": identifier}), 1)
                        row = (await connection.execute(text("""
                            SELECT c.status, q.status AS review_status, q.was_accepted
                            FROM decision_tree.user_corrections c
                            JOIN decision_tree.review_queue q ON q.review_id=c.id WHERE c.id=:id
                        """), {"id": identifier})).mappings().one()
                        self.assertEqual(row["status"], "pending")
                        self.assertEqual(row["review_status"], "pending")
                        self.assertFalse(row["was_accepted"])
                        data.corrections[0].corrected_text = "E202"
                        with self.assertRaises(HTTPException) as error:
                            await service.save(data)
                        self.assertEqual(error.exception.status_code, 409)
                        self.assertEqual(await connection.scalar(text("SELECT count(*) FROM decision_tree.training_data")), baseline)
                finally:
                    await transaction.rollback()
                self.assertEqual(await connection.scalar(text(
                    "SELECT count(*) FROM decision_tree.user_corrections WHERE id=:id"), {"id": identifier}), 0)
                self.assertEqual(await connection.scalar(text(
                    "SELECT count(*) FROM decision_tree.review_queue WHERE review_id=:id"), {"id": identifier}), 0)
        finally:
            await engine.dispose()
