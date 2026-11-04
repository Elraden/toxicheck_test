"""Export proE facts and short attributed excerpts; never connect to a database.

Run from backend: python -m app.db.scrape_proe
The resumable cache contains parsed records, not copies of complete articles.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from bs4 import BeautifulSoup

from app.services.normalization import normalize_text


ROOT = Path(__file__).resolve().parents[3]
BASE = "https://proe.info"
INDEX = BASE + "/ru/additives"
DATASET = "toxicheck_proe"
CODE_RE = re.compile(r"E\d{3,4}[a-z]?(?:\([ivx]+\))?", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def stable_id(kind, key):
    return str(uuid5(NAMESPACE_URL, f"toxicheck/proe/v1/{kind}/{key}"))


def clean(value):
    return re.sub(r"\s+", " ", value.replace("\u200b", "")).strip()


def text_of(node):
    return clean(node.get_text(" ", strip=True)) if node else None


def links(node, selector):
    return [dict(title=text_of(a), url=urljoin(BASE, a["href"]))
            for a in node.select(selector) if a.get("href")]


def short_description(record):
    description = f"{record['name_ru']} ({record['e_code']})."
    if record["categories"]:
        description += " Функции: " + "; ".join(c["title"] for c in record["categories"]) + "."
    if record["origins"]:
        description += " " + "; ".join(o["title"] for o in record["origins"]) + "."
    return description


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


class Fetcher:
    def __init__(self, delay):
        self.delay = delay
        self.lock = threading.Lock()
        self.last_request = 0.0

    def get(self, url):
        if urlsplit(url).netloc != "proe.info":
            raise ValueError(f"Unexpected crawl host: {url}")
        for attempt in range(4):
            with self.lock:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
            try:
                request = Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ToxiCheckDataImporter/1.0)",
                    "Accept-Language": "ru",
                })
                with urlopen(request, timeout=40) as response:
                    if urlsplit(response.url).netloc != "proe.info":
                        raise ValueError(f"Unexpected redirect: {response.url}")
                    return response.read().decode("utf-8")
            except (HTTPError, URLError, TimeoutError) as error:
                if isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt == 3:
                    raise
                retry_after = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) else ""
                time.sleep(min(120, int(retry_after)) if retry_after.isdigit() else 2 ** (attempt + 1))


def parse_catalog(html, url):
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for a in soup.select("a.addicon__link[href]"):
        code_node = a.parent.select_one(".addicon__name")
        code = text_of(code_node)
        if not code or not CODE_RE.fullmatch(code):
            raise ValueError(f"Invalid catalog code: {code!r}")
        entries[urljoin(url, a["href"])] = code
    if not entries:
        raise ValueError(f"No additive cards at {url}")
    next_link = soup.select_one(".pager a[href]")
    return entries, urljoin(url, next_link["href"]) if next_link else None


def parse_additive(html, url, expected_code):
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.node--additive")
    if not article:
        raise ValueError(f"Missing additive article: {url}")
    code = text_of(article.select_one(".addicon__name"))
    if code != expected_code:
        raise ValueError(f"Code mismatch: {expected_code} != {code} at {url}")
    heading = text_of(soup.select_one("h1"))
    if not heading or not heading.startswith(code):
        raise ValueError(f"Missing additive title: {url}")
    name = re.sub(r"^[\s–—-]+", "", heading[len(code):])
    origins = links(article, ".addprop--origin a")
    categories = links(article, ".addprop--category a")
    synonyms_node = article.select_one(".field--additive-synonyms .field__content")
    synonyms = [clean(s) for s in re.split(r"[,;\n]", text_of(synonyms_node) or "") if clean(s)]
    body = article.select_one(".field--additive-info")
    sections = []
    excerpt = None
    excerpt_truncated = False
    if body:
        for noise in body.select("script, style, .block--advert-block, ins"):
            noise.decompose()
        sections = [text_of(h) for h in body.select("h2, h3, h4")]
        first = next((value for p in body.select("p") if (value := text_of(p))), None)
        if first:
            # Keep at most 25 quoted words per article; full articles stay at source.
            words = first.split()
            excerpt_truncated = len(words) > 25
            excerpt = " ".join(words[:25]) + ("…" if excerpt_truncated else "")
    references = {}
    if body:
        for ref in links(body, "a[href]"):
            if urlsplit(ref["url"]).path.startswith("/ru/additives/"):
                continue
            if urlsplit(ref["url"]).scheme in {"http", "https"}:
                references[ref["url"]] = {**ref, "relation": "article_link"}
    for node in soup.select("#block-views-block-related-documents-related-documents .node__content"):
        a = node.select_one(".field--node-link a[href]")
        if a:
            ref_url = urljoin(url, a["href"])
            references[ref_url] = dict(
                title=text_of(a), url=ref_url, relation="mentioned_in_document",
                organization=text_of(node.select_one(".field--country-organization")),
            )
    related = []
    if body:
        related = sorted({urljoin(url, a["href"]) for a in body.select('a[href^="/ru/additives/e"]')
                          if re.fullmatch(r"/ru/additives/e\d+[a-z]*", urlsplit(a["href"]).path)})
    description = short_description(dict(name_ru=name, e_code=code, categories=categories, origins=origins))
    return dict(
        e_code=code, name_ru=name, source_url=url, fetched_at=now(),
        page_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        description=description, description_excerpt=excerpt,
        excerpt_truncated=excerpt_truncated, has_article=bool(excerpt),
        origins=origins, categories=categories, synonyms=synonyms,
        section_titles=sections, references=list(references.values()),
        related_additive_urls=related,
    )


def parse_document(html, url):
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article")
    if not article:
        raise ValueError(f"Missing document article: {url}")
    external = []
    files = []
    for a in article.select("a[href]"):
        target = urljoin(url, a["href"])
        parts = urlsplit(target)
        if parts.scheme in {"http", "https"} and parts.netloc != "proe.info":
            external.append(target)
        elif parts.netloc == "proe.info" and parts.path.startswith("/files/"):
            files.append(target)
    return dict(url=url, title=text_of(soup.select_one("h1")),
                external_urls=sorted(set(external)), file_urls=sorted(set(files)), fetched_at=now(),
                verification_status="links_extracted_not_independently_verified")


def cached_parse(fetcher, cache, url, parse, refresh):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    value = parse(fetcher.get(url))
    write_json(path, value)
    return value


def build_bundle(records, documents, catalog_pages, failures, *, descriptions=None):
    if descriptions is None:
        path = ROOT / "backend/db/proe_descriptions.json"
        descriptions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    timestamp = now()
    source_id = stable_id("source", INDEX)
    bundle = dict(
        metadata=dict(dataset=DATASET, schema_version=3, generated_at=timestamp,
                      language="ru", source_url=INDEX, target_database="toxicheck_proe",
                      catalog_pages=catalog_pages,
                      description_policy="factual_summary_and_excerpt_up_to_25_words",
                      full_description_policy="authored_paraphrase_of_proe_article_not_independently_verified"),
        regulatory_sources=[dict(id=source_id, code="PROE_INFO", title="проЕ — справочник пищевых добавок",
                                 url=INDEX, version_date=None, effective_from=None, effective_to=None)],
        ingredients=[], ingredient_aliases=[], ingredient_rules=[], source_fragments=[],
    )
    for record in records:
        code = record["e_code"]
        # Older resumable cache records contain the publisher's rating.
        # Reconstruct text from facts and drop that field before exporting.
        record = {key: value for key, value in record.items() if key != "danger"}
        record["description"] = short_description(record)
        full_description = descriptions.get(code) if record["has_article"] else None
        record = {**record, "full_description": full_description,
                  "full_description_status": ("paraphrased" if full_description else
                                              "not_reviewed" if record["has_article"] else "no_source_article")}
        ingredient_id = stable_id("ingredient", code)
        bundle["ingredients"].append(dict(
            id=ingredient_id, kind="food_additive", e_code=code,
            canonical_name_ru=record["name_ru"], canonical_name_en=None,
            category="; ".join(c["title"] for c in record["categories"]) or None,
            description=record["description"], full_description=full_description, is_active=True,
        ))
        aliases = [code, record["name_ru"], *record["synonyms"]]
        for alias in dict.fromkeys(aliases):
            bundle["ingredient_aliases"].append(dict(
                id=stable_id("alias", code + "/" + alias), ingredient_id=ingredient_id,
                alias=alias, normalized_alias=normalize_text(alias),
                language="ru" if re.search("[а-яёА-ЯЁ]", alias) else "en",
                source="PROE_INFO", confidence=1,
            ))
        bundle["source_fragments"].append(dict(
            id=stable_id("fragment", record["source_url"]), source_id=source_id,
            ingredient_id=ingredient_id, record_kind="additive_profile", **record,
        ))
    for doc in documents:
        bundle["source_fragments"].append(dict(
            id=stable_id("fragment", doc["url"]), source_id=source_id,
            record_kind="document_reference", **doc,
        ))
    bundle["validation"] = dict(
        complete=not failures, failures=failures, additives=len(records),
        catalog_entries=sum(page["count"] for page in catalog_pages),
        with_article=sum(r["has_article"] for r in records),
        with_full_description=sum(bool(i["full_description"]) for i in bundle["ingredients"]),
        without_full_description=[i["e_code"] for i in bundle["ingredients"] if not i["full_description"]],
        without_article=[r["e_code"] for r in records if not r["has_article"]],
        documents=len(documents),
    )
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle):
    if bundle["metadata"]["dataset"] != DATASET:
        raise ValueError("Unexpected dataset")
    for table in ("ingredients", "regulatory_sources", "ingredient_aliases", "ingredient_rules", "source_fragments"):
        ids = [r["id"] for r in bundle[table]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate IDs: {table}")
    ingredient_ids = {r["id"] for r in bundle["ingredients"]}
    source_ids = {r["id"] for r in bundle["regulatory_sources"]}
    codes = [r["e_code"] for r in bundle["ingredients"]]
    if len(codes) != len(set(codes)) or not all(CODE_RE.fullmatch(c) for c in codes):
        raise ValueError("Invalid or duplicate E codes")
    for table in ("ingredient_aliases", "ingredient_rules", "source_fragments"):
        for row in bundle[table]:
            if "ingredient_id" in row and row["ingredient_id"] not in ingredient_ids:
                raise ValueError(f"Orphan ingredient: {row['id']}")
            if "source_id" in row and row["source_id"] not in source_ids:
                raise ValueError(f"Orphan source: {row['id']}")
    profiles = [r for r in bundle["source_fragments"] if r["record_kind"] == "additive_profile"]
    if len(profiles) != len(codes):
        raise ValueError("Missing additive profiles")
    if {p["ingredient_id"] for p in profiles} != ingredient_ids:
        raise ValueError("Profiles do not cover every ingredient")
    if bundle["validation"]["complete"]:
        if bundle["validation"]["failures"] or len(codes) != bundle["validation"]["catalog_entries"]:
            raise ValueError("Complete export must cover the entire catalog without failures")
    for row in profiles:
        if len((row["description_excerpt"] or "").split()) > 25:
            raise ValueError("Excerpt exceeds limit")
    if bundle["metadata"].get("schema_version", 1) >= 2:
        by_id = {i["id"]: i for i in bundle["ingredients"]}
        for row in profiles:
            full = row.get("full_description")
            if full != by_id[row["ingredient_id"]].get("full_description"):
                raise ValueError("Ingredient/profile descriptions differ")
            if full and (not row["has_article"] or not full.strip()):
                raise ValueError("Full description requires an article and non-empty content")
    if bundle["metadata"].get("schema_version", 1) >= 3:
        if bundle["ingredient_rules"] or any("danger" in r for r in profiles):
            raise ValueError("Publisher ratings must not be exported")
        if "severity_mapping" in bundle["metadata"] or any("Оценка proE:" in r["description"] for r in profiles):
            raise ValueError("Publisher rating text/mapping must not be exported")
    return bundle["validation"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "toxicheck_proe.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "backend/data/proe_cache")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--refresh-documents", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()
    if args.validate_only:
        report = validate_bundle(json.loads(args.output.read_text(encoding="utf-8")))
        print(json.dumps({key: len(value) if isinstance(value, list) else value
                          for key, value in report.items()}, ensure_ascii=False, indent=2))
        return
    fetcher = Fetcher(max(0.25, args.delay))
    entries, pages, seen = {}, [], set()
    url = INDEX
    while url:
        if url in seen or len(seen) >= 30:
            raise ValueError("Catalog pagination cycle")
        seen.add(url)
        page = cached_parse(fetcher, args.cache_dir, url,
                            lambda html: dict(zip(("entries", "next"), parse_catalog(html, url))), args.refresh)
        overlap = entries.keys() & page["entries"].keys()
        if overlap:
            raise ValueError(f"Duplicate catalog pages: {url}")
        pages.append(dict(url=url, count=len(page["entries"])))
        entries.update(page["entries"])
        print(f"Catalog page {len(pages)}: {len(page['entries'])} entries", flush=True)
        url = page["next"]
    records, documents, failures = [], [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cached_parse, fetcher, args.cache_dir, url,
                               lambda html, u=url, c=code: parse_additive(html, u, c), args.refresh): url
                   for url, code in entries.items()}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                records.append(future.result())
            except Exception as error:
                failures.append(dict(url=futures[future], error=str(error)))
                print(f"ERROR {futures[future]}: {error}", flush=True)
            if index % 25 == 0 or index == len(entries):
                print(f"Additives: {index}/{len(entries)}, failed: {len(failures)}", flush=True)
    document_urls = sorted({ref["url"] for record in records for ref in record["references"]
                            if ref["url"].startswith(BASE + "/ru/documents/")})
    for index, url in enumerate(document_urls, 1):
        try:
            documents.append(cached_parse(fetcher, args.cache_dir, url,
                                          lambda html: parse_document(html, url),
                                          args.refresh or args.refresh_documents))
        except Exception as error:
            failures.append(dict(url=url, error=str(error)))
        if index % 10 == 0 or index == len(document_urls):
            print(f"Documents: {index}/{len(document_urls)}", flush=True)
    order = {code: index for index, code in enumerate(entries.values())}
    records.sort(key=lambda r: order[r["e_code"]])
    bundle = build_bundle(records, documents, pages, failures)
    if failures:
        partial = args.output.with_name(args.output.stem + ".partial.json")
        write_json(partial, bundle)
        raise SystemExit(f"Incomplete scrape saved to {partial}; rerun to retry failures.")
    write_json(args.output, bundle)
    print(f"Saved {args.output}: {len(records)} additives, {len(documents)} documents", flush=True)


if __name__ == "__main__":
    main()
