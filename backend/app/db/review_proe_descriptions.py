"""Collect bounded reading notes for authoring proE summaries (no DB writes)."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from bs4 import BeautifulSoup

from app.db.scrape_proe import Fetcher, ROOT, clean, write_json


def reading_notes(html):
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("article.node--additive .field--additive-info")
    if not body:
        return {}
    for noise in body.select("script, style, .block--advert-block, ins"):
        noise.decompose()
    sections = {}
    heading = "Введение"
    for node in body.find_all(["h2", "h3", "h4", "p", "li"]):
        text = clean(node.get_text(" ", strip=True))
        if node.name.startswith("h"):
            heading = text
        elif text:
            sections.setdefault(heading, []).append(text)
    return {key: " ".join(values)[:1600] for key, values in sections.items()}


def main():
    payload = json.loads((ROOT / "toxicheck_proe.json").read_text(encoding="utf-8"))
    records = [r for r in payload["source_fragments"]
               if r["record_kind"] == "additive_profile" and r["has_article"]]
    directory = ROOT / "backend/data/proe_cache/reading"
    fetcher = Fetcher(0.5)

    def collect(record):
        path = directory / (record["e_code"] + ".json")
        if path.exists():
            return
        sections = reading_notes(fetcher.get(record["source_url"]))
        if not sections:
            raise ValueError(f"Missing article: {record['e_code']}")
        write_json(path, dict(e_code=record["e_code"], source_url=record["source_url"], sections=sections))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(collect, record) for record in records]
        for index, future in enumerate(as_completed(futures), 1):
            future.result()
            if index % 25 == 0 or index == len(records):
                print(f"Reading notes: {index}/{len(records)}", flush=True)


if __name__ == "__main__":
    main()
