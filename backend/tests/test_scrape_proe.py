import copy
import unittest

from app.db.scrape_proe import (
    build_bundle, parse_additive, parse_catalog, parse_document, validate_bundle,
)


def card(code="E100(i)", danger=0, body=True):
    rating = (f'<div class="addprop--danger"><a class="addprop-item--danger-{danger}">'
              'Нулевая опасность</a></div>') if danger is not None else ""
    return f'''<h1>{code} – Название добавки</h1>
    <article class="node--additive"><div class="addicon__name">{code}</div>
    {rating}<div class="addprop--origin"><a href="/ru/additives/origin">Происхождение</a></div>
    <div class="addprop--category"><a href="/ru/additives/category">Категория</a></div>
    <div class="field--additive-synonyms"><div class="field__content">Е100(і), E-100(i)</div></div>
    {('<div class="field--additive-info"><h2>Общая информация</h2><p>' + 'слово ' * 60 +
      '</p><h3>Вред</h3><a href="https://example.org/paper">Исследование</a>' +
      '<div class="block--advert-block"><a href="https://ad.example">Реклама</a></div></div>') if body else ''}
    </article><aside><a href="https://unrelated.example">Меню</a></aside>'''


class ProeParserTests(unittest.TestCase):
    def test_subtype_and_scoped_references_without_publisher_rating(self):
        row = parse_additive(card(), "https://proe.info/ru/additives/e100i", "E100(i)")
        self.assertEqual(row["e_code"], "E100(i)")
        self.assertNotIn("danger", row)
        self.assertNotIn("Оценка proE", row["description"])
        self.assertEqual(len(row["description_excerpt"].split()), 25)
        self.assertTrue(row["excerpt_truncated"])
        self.assertEqual([r["url"] for r in row["references"]], ["https://example.org/paper"])

    def test_missing_rating_and_article_are_not_invented(self):
        row = parse_additive(card(danger=None, body=False), "https://proe.info/ru/additives/e100i", "E100(i)")
        self.assertNotIn("danger", row)
        self.assertIsNone(row["description_excerpt"])
        bundle = build_bundle([row], [], [{"count": 1}], [])
        self.assertEqual(bundle["ingredient_rules"], [])
        self.assertEqual(bundle["validation"]["without_article"], ["E100(i)"])
        self.assertIsNone(bundle["ingredients"][0]["full_description"])
        self.assertEqual(bundle["source_fragments"][0]["full_description_status"], "no_source_article")

    def test_empty_leading_paragraph_does_not_hide_article(self):
        html = card().replace('<h2>Общая информация</h2>', '<p>\u200b</p><h2>Общая информация</h2>')
        row = parse_additive(html, "https://proe.info/ru/additives/e100i", "E100(i)")
        self.assertTrue(row["has_article"])
        self.assertTrue(row["description_excerpt"].startswith("слово"))

    def test_wrong_page_fails_but_unrecognized_rating_is_ignored(self):
        with self.assertRaises(ValueError):
            parse_additive(card(), "https://proe.info/ru/additives/e101", "E101")
        row = parse_additive(card(danger=8), "https://proe.info/ru/additives/e100i", "E100(i)")
        self.assertNotIn("danger", row)

    def test_catalog_pagination_and_terminal_page(self):
        html = '<div><span class="addicon__name">E100(i)</span><a class="addicon__link" href="/ru/additives/e100i"></a></div>'
        entries, next_url = parse_catalog(html + '<ul class="pager"><a href="?page=1">Еще</a></ul>', "https://proe.info/ru/additives")
        self.assertEqual(list(entries.values()), ["E100(i)"])
        self.assertEqual(next_url, "https://proe.info/ru/additives?page=1")
        self.assertIsNone(parse_catalog(html, "https://proe.info/ru/additives?page=2")[1])
        with self.assertRaises(ValueError):
            parse_catalog("<h1>Access denied</h1>", "https://proe.info")

    def test_document_links_are_not_fetched_or_claimed_verified(self):
        doc = parse_document('<h1>Документ</h1><article><a href="https://example.org/source">Источник</a><a href="/files/docs/source.pdf">PDF</a></article><a href="https://ad.example">Реклама</a>', "https://proe.info/ru/documents/test")
        self.assertEqual(doc["external_urls"], ["https://example.org/source"])
        self.assertEqual(doc["file_urls"], ["https://proe.info/files/docs/source.pdf"])
        self.assertIn("not_independently_verified", doc["verification_status"])

    def test_bundle_ids_and_foreign_keys(self):
        row = parse_additive(card(), "https://proe.info/ru/additives/e100i", "E100(i)")
        first = build_bundle([row], [], [{"count": 1}], [])
        second = build_bundle([row], [], [{"count": 1}], [])
        self.assertEqual(first["ingredients"], second["ingredients"])
        broken = copy.deepcopy(first)
        broken["ingredient_aliases"][0]["ingredient_id"] = "missing"
        with self.assertRaises(ValueError):
            validate_bundle(broken)
        broken = copy.deepcopy(first)
        broken["ingredients"].append(broken["ingredients"][0])
        with self.assertRaises(ValueError):
            validate_bundle(broken)

    def test_old_cache_ratings_do_not_reappear_on_rebuild(self):
        row = parse_additive(card(), "https://proe.info/ru/additives/e100i", "E100(i)")
        row["danger"] = {"level": 4, "label": "Высокая опасность"}
        row["description"] += " Оценка proE: высокая опасность."
        summary = "Подробный пересказ свойств вещества и его применения в пищевой рецептуре. " * 4
        bundle = build_bundle([row], [], [{"count": 1}], [], descriptions={"E100(i)": summary})
        profile = bundle["source_fragments"][0]
        self.assertNotIn("danger", profile)
        self.assertNotIn("Оценка proE", profile["description"])
        self.assertEqual(bundle["ingredient_rules"], [])
        self.assertEqual(bundle["ingredients"][0]["full_description"], summary)
        self.assertEqual(profile["full_description"], summary)
        self.assertEqual(bundle["validation"]["with_full_description"], 1)
        self.assertNotIn("severity_mapping", bundle["metadata"])

    def test_summary_missing_for_new_article_is_explicit(self):
        row = parse_additive(card(), "https://proe.info/ru/additives/e100i", "E100(i)")
        bundle = build_bundle([row], [], [{"count": 1}], [], descriptions={})
        self.assertIsNone(bundle["ingredients"][0]["full_description"])
        self.assertEqual(bundle["source_fragments"][0]["full_description_status"], "not_reviewed")

    def test_inconsistent_summary_is_rejected(self):
        row = parse_additive(card(), "https://proe.info/ru/additives/e100i", "E100(i)")
        bundle = build_bundle([row], [], [{"count": 1}], [])
        bundle["ingredients"][0]["full_description"] = "Другой текст"
        with self.assertRaises(ValueError):
            validate_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
