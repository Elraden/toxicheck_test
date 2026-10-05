import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.db.session import get_catalog_session
from app.main import create_app
from app.schemas.ingredients import IngredientRuleOut
from app.services.ingredient_catalog import IngredientCatalog
from app.services.ingredient_resolver import IngredientResolver


ID = "c303e9aa-a0e1-5501-a16d-317364f6b8ef"


def rows(items):
    result = MagicMock()
    result.mappings.return_value.all.return_value = items
    result.mappings.return_value.first.return_value = items[0] if items else None
    return result


class CatalogServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_filter_precedes_pagination_and_uses_highest_assessment(self):
        ids = ["neutral", "attention", "mixed", "avoid", "forbidden", "unknown"]
        rules = {
            identifier: [IngredientRuleOut(id=identifier, rule_type="test", severity=severity,
                         assessment_severity=severity, title="Test")]
            for identifier, severity in zip(ids[:-1], ["neutral", "attention", "neutral", "avoid", "forbidden"])
        }
        rules["mixed"].append(rules["attention"][0])
        expected = {"neutral": ["neutral"], "attention": ["attention", "mixed"],
                    "restricted": ["avoid", "forbidden"], "unknown": ["unknown"]}
        for status, matches in expected.items():
            with self.subTest(status=status):
                session = AsyncMock()
                candidates = MagicMock()
                candidates.scalars.return_value.all.return_value = ids
                # Page two must still count matches from the entire search result.
                session.execute.side_effect = [candidates, rows([])]
                catalog = IngredientCatalog(session)
                catalog.resolver.catalog_rules = AsyncMock(return_value=rules)
                page = await catalog.list("sorbate", "additives", 1, 1, status=status)
                self.assertEqual((page.total, page.limit, page.offset), (len(matches), 1, 1))
                catalog.resolver.catalog_rules.assert_awaited_once_with(ids)
                first = session.execute.await_args_list[0]
                self.assertNotIn("LIMIT", str(first.args[0]))
                self.assertEqual(first.args[1]["query"], "sorbate")
                self.assertEqual(first.args[1]["kind"], "additives")
                last = session.execute.await_args
                self.assertEqual(last.args[1]["matching_ids"], matches)
                self.assertEqual(last.args[1]["offset"], 1)
                self.assertIn("ANY(CAST(:matching_ids AS uuid[]))", str(last.args[0]))

    async def test_status_without_matches_returns_empty_page(self):
        session = AsyncMock()
        candidates = MagicMock()
        candidates.scalars.return_value.all.return_value = [ID]
        session.execute.return_value = candidates
        catalog = IngredientCatalog(session)
        catalog.resolver.catalog_rules = AsyncMock(return_value={})
        page = await catalog.list("", "all", 30, 0, status="neutral")
        self.assertEqual(page.total, 0)
        self.assertEqual(page.items, [])
        session.execute.assert_awaited_once()

    async def test_paginated_search_is_bound_and_rules_are_batched(self):
        session = AsyncMock()
        session.scalar.return_value = 101
        session.execute.return_value = rows([dict(id=ID, name="Water", code=None, category=None)])
        catalog = IngredientCatalog(session)
        catalog.resolver.catalog_rules = AsyncMock(return_value={})
        query = "%' OR true --"
        page = await catalog.list(query, "all", 30, 60)
        self.assertEqual((page.total, page.limit, page.offset), (101, 30, 60))
        self.assertEqual(page.items[0].severity, "unknown")
        catalog.resolver.catalog_rules.assert_awaited_once_with([ID])
        call = session.execute.await_args
        self.assertNotIn(query, str(call.args[0]))
        self.assertEqual(call.args[1]["query"], query.lower())
        self.assertEqual(call.args[1]["offset"], 60)
        self.assertIn("i.is_active", str(call.args[0]))
        self.assertIn("ORDER BY", str(call.args[0]))

    async def test_cyrillic_e_code_is_normalized_for_search(self):
        session = AsyncMock()
        session.scalar.return_value = 0
        session.execute.return_value = rows([])
        page = await IngredientCatalog(session).list("Е-202", "additives", 30, 0)
        self.assertEqual(page.items, [])
        self.assertEqual(session.execute.await_args.args[1]["code"], "e202")

    async def test_detail_preserves_descriptions_tags_and_assessed_rules(self):
        session = AsyncMock()
        aliases = MagicMock()
        aliases.scalars.return_value.all.return_value = ["Potassium sorbate"]
        session.execute.side_effect = [
            rows([dict(id=ID, name="Sorbate", code="E202", category="Preservative",
                       name_en=None, description="Short text", full_description="Long text")]),
            rows([dict(tag_type="category", name_ru="Preservative"), dict(tag_type="origin", name_ru="Synthetic")]),
            aliases,
        ]
        catalog = IngredientCatalog(session)
        rule = IngredientRuleOut(id="rule", rule_type="regulatory_status", severity="attention",
            assessment_severity="attention", title="Needs review", citation="Appendix 1",
            conditions={"primary_basis_required": True, "evidence": [{"locator": "Appendix 1"}]})
        catalog.resolver.catalog_rules = AsyncMock(return_value={ID: [rule]})
        item = await catalog.detail(ID)
        self.assertEqual(item.full_description, "Long text")
        self.assertEqual(item.functions, ["Preservative"])
        self.assertEqual(item.origins, ["Synthetic"])
        self.assertEqual(item.aliases, ["Potassium sorbate"])
        self.assertEqual(item.severity, "attention")
        self.assertTrue(item.rules[0].conditions["primary_basis_required"])
        self.assertEqual(item.rules[0].citation, "Appendix 1")

    async def test_missing_or_inactive_detail_returns_none(self):
        session = AsyncMock()
        session.execute.return_value = rows([])
        self.assertIsNone(await IngredientCatalog(session).detail(ID))
        session.execute.assert_awaited_once()
        self.assertIn("AND is_active", str(session.execute.await_args.args[0]))

    async def test_catalog_and_scan_use_same_assessment(self):
        rule = IngredientRuleOut(id="rule", rule_type="regulatory_status", severity="regulatory",
                                 title="Reference", conditions={"evaluation": "reference_only"})
        resolver = IngredientResolver(AsyncMock())
        resolver._load_rules = AsyncMock(return_value={ID: [rule]})
        catalog_rules = (await resolver.catalog_rules([ID]))[ID]
        self.assertEqual(catalog_rules, resolver.assess_rules([rule], "alias"))
        self.assertEqual(resolver.highest_severity(catalog_rules), "neutral")
        self.assertEqual(resolver.highest_severity([]), "unknown")


class CatalogEndpointTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.session = AsyncMock()

        async def session():
            yield self.session

        self.app.dependency_overrides[get_catalog_session] = session
        self.client = TestClient(self.app)

    def test_invalid_filters_pagination_and_identifier(self):
        for path in ("?limit=0", "?limit=101", "?offset=-1", "?kind=invalid", "?status=invalid", "?q=" + "a" * 201, "/invalid"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/api/ingredients" + path).status_code, 422)
        self.session.execute.assert_not_awaited()

    def test_missing_detail_is_404(self):
        with patch.object(IngredientCatalog, "detail", new_callable=AsyncMock, return_value=None):
            self.assertEqual(self.client.get("/api/ingredients/" + ID).status_code, 404)

    def test_status_is_forwarded_to_catalog(self):
        for status in ("all", "neutral", "attention", "restricted", "unknown"):
            with self.subTest(status=status), patch.object(IngredientCatalog, "list", new_callable=AsyncMock,
                    return_value={"items": [], "total": 0, "limit": 30, "offset": 0}) as listing:
                response = self.client.get(f"/api/ingredients?status={status}&kind=additives&q=E202")
                self.assertEqual(response.status_code, 200)
                listing.assert_awaited_once_with("E202", "additives", 30, 0, status=status)

    def test_list_contract(self):
        self.session.scalar.return_value = 1
        self.session.execute.return_value = rows([dict(id=ID, name="Water", code=None, category=None)])
        with patch.object(IngredientResolver, "catalog_rules", new_callable=AsyncMock, return_value={}):
            response = self.client.get("/api/ingredients?limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["severity"], "unknown")
        self.assertEqual(response.json()["total"], 1)
