"""OCR matching adapter for the application's catalog and annotation database."""
import asyncio
import logging
import re
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from sqlalchemy import text
from app.db.session import AnnotationSessionLocal
from app.schemas.ingredients import MatchedIngredientOut
from app.schemas.ocr import HazardFlag
from app.services.ingredient_resolver import IngredientResolver
from app.services.normalization import normalize_text, normalize_e_code
from app.services.ocr_matching import match_score, is_uncertain, MATCH_THRESHOLD, levenshtein_distance

log = logging.getLogger(__name__)
# Numeric identifiers are never fuzzy-matched to another identifier.
CODE_LIKE = re.compile(r"(?<!\w)[eе]\s*[-?.,:!]?\s*\d", re.I)
NEGATION = re.compile(r"\b(?:без|не\s+содержит|without|free\s+from)\b", re.I)


def rank_candidates(raw, rows):
    token = normalize_text(raw)
    ranked = {}
    for row in rows:
        score = match_score(token, row["normalized_alias"])
        identifier = str(row["ingredient_id"])
        if identifier not in ranked or score > ranked[identifier][0]:
            ranked[identifier] = (score, row)
    ordered = sorted(ranked.values(), key=lambda item: (-item[0], item[1]["ingredient_id"]))
    if not ordered:
        return None, 0.0, False
    score, best = ordered[0]
    ambiguous = len(ordered)>1 and abs(score-ordered[1][0])<1e-9
    return best, score, ambiguous


class OcrIngredientResolver(IngredientResolver):
    def __init__(self, session):
        super().__init__(session)
        self.review_candidates = []

    async def _candidates(self, tokens):
        if not tokens:
            return {}
        result = await self.session.execute(text("""
            SELECT input.token, candidate.*
            FROM unnest(CAST(:tokens AS text[])) AS input(token)
            CROSS JOIN LATERAL (
                SELECT a.normalized_alias, i.id::text AS ingredient_id,
                       i.canonical_name_ru AS name, i.e_code AS code
                FROM catalog.ingredient_aliases a
                JOIN catalog.ingredients i ON i.id=a.ingredient_id
                WHERE i.is_active AND a.normalized_alias % input.token
                  AND NOT EXISTS (SELECT 1 FROM catalog.ingredient_alias_review r
                                  WHERE r.normalized_alias=a.normalized_alias)
                  AND NOT EXISTS (SELECT 1 FROM catalog.ingredient_aliases other
                                  WHERE other.normalized_alias=a.normalized_alias
                                    AND other.ingredient_id<>a.ingredient_id)
                ORDER BY similarity(a.normalized_alias,input.token) DESC, i.id, a.normalized_alias
                LIMIT 24
            ) AS candidate
        """), {"tokens": tokens})
        grouped = {}
        for row in result.mappings().all():
            grouped.setdefault(row["token"], []).append(dict(row))
        return grouped

    async def resolve(self, ingredients_text):
        exact = await super().resolve(ingredients_text)
        # Keep raw tokens and the existing E-code subtype/negation protections.
        eligible = [raw for raw in exact.unmatched
                    if 3 <= len(normalize_text(raw)) <= 160
                    and not CODE_LIKE.search(raw) and not NEGATION.search(raw)][:100]
        candidates = await self._candidates(sorted({normalize_text(raw) for raw in eligible}))
        def rank_all():
            return [(raw, *rank_candidates(raw, candidates.get(normalize_text(raw), []))) for raw in eligible]
        ranked = await asyncio.to_thread(rank_all)
        accepted = []
        for raw, best, score, ambiguous in ranked:
            if best is None:
                continue
            accept = score >= MATCH_THRESHOLD and not ambiguous
            if is_uncertain(score) or ambiguous:
                self.review_candidates.append(dict(token_raw=raw, candidate_canonical_name=best["name"],
                    match_score=round(score,4), was_accepted=accept, is_hazard=False))
            if accept:
                accepted.append((raw, best, score))
        rules = await self._load_rules(sorted({row["ingredient_id"] for _, row, _ in accepted}))
        seen = {item.ingredient_id for item in exact.matched}
        for raw, row, score in accepted:
            identifier = row["ingredient_id"]
            exact.unmatched.remove(raw)
            if identifier in seen:
                continue
            seen.add(identifier)
            assessed = self.assess_rules(rules.get(identifier, []), "similarity")
            exact.matched.append(MatchedIngredientOut(ingredient_id=identifier, name=row["name"],
                code=row["code"], raw_text=raw, matched_by="similarity", match_score=round(score,4),
                severity=self.highest_severity(assessed), rules=assessed))
        return exact


# The ML prototype's noisy-code detector, with explicit uncertainty and no static blacklist.
NOISY_CODE = re.compile(r"(?<!\w)[eе][\s\-.,:;?!'\"«»]{0,2}"
    r"(\d{1,2}[\s\-.,:;?!'\"«»Ээ]?\d{1,3})([a-zа-я]?)(?!\w|\s*\([ivx])", re.I)


def find_hazards(raw, banned, known_codes, confidence):
    found = {}
    for match in NOISY_CODE.finditer(raw):
        prefix = re.split(r"[,;.\n]", raw[max(0,match.start()-60):match.start()])[-1]
        if NEGATION.search(prefix):
            continue
        literal = normalize_e_code(match.group())
        candidate = normalize_e_code("E"+re.sub(r"\D", "", match.group(1))+match.group(2))
        if not candidate:
            continue
        candidate = candidate.lower()
        # E211 must not be reported as E216 simply because one digit differs.
        if candidate in known_codes and candidate not in banned:
            continue
        targets = [candidate] if candidate in banned else [
            code for code in sorted(banned) if levenshtein_distance(candidate[1:],code[1:])<=1]
        for code in targets[:3]:
            entry = banned[code]
            certainty = "high" if literal and literal.lower()==code and confidence>=0.45 else "needs_verification"
            finding = HazardFlag(tokenRaw=match.group(), eCode=entry["code"], name=entry["name"],
                ingredientId=entry["ingredient_id"], certainty=certainty,
                citations=entry["citations"])
            found[(match.group(),code)] = finding
    return list(found.values())


async def catalog_hazards(session, raw, confidence):
    if not CODE_LIKE.search(raw):
        return []
    rows = (await session.execute(text("""
        SELECT id::text AS ingredient_id, canonical_name_ru AS name, e_code AS code
        FROM catalog.ingredients WHERE is_active AND e_code IS NOT NULL
    """))).mappings().all()
    known = {row["code"].lower() for row in rows}
    candidates = (await session.scalars(text("""
        SELECT DISTINCT r.ingredient_id::text
        FROM catalog.ingredient_rules r JOIN catalog.regulatory_sources s ON s.id=r.source_id
        WHERE r.regulatory_status='BANNED'
          AND (r.effective_from IS NULL OR r.effective_from<=CURRENT_DATE)
          AND (r.effective_to IS NULL OR r.effective_to>=CURRENT_DATE)
          AND (s.effective_from IS NULL OR s.effective_from<=CURRENT_DATE)
          AND (s.effective_to IS NULL OR s.effective_to>=CURRENT_DATE)
    """))).all()
    assessed = await IngredientResolver(session).catalog_rules(candidates)
    banned = {}
    for row in rows:
        rules = [r for r in assessed.get(row["ingredient_id"], []) if r.assessment_severity=="forbidden"]
        if rules:
            banned[row["code"].lower()] = {**row, "citations":list(dict.fromkeys(
                r.citation or r.source_title or r.title for r in rules))}
    return find_hazards(raw, banned, known, confidence or 0)


async def enqueue_reviews(request_id, candidates):
    if not candidates:
        return 0
    count = 0
    async with AnnotationSessionLocal() as db, db.begin():
        for item in candidates[:100]:
            identity = uuid5(NAMESPACE_URL, str(request_id)+"|"+item["token_raw"]+"|"+
                             item["candidate_canonical_name"]+"|"+str(item["is_hazard"]))
            inserted = await db.scalar(text("""
                INSERT INTO decision_tree.review_queue
                    (review_id,request_id,token_raw,candidate_canonical_name,match_score,was_accepted,is_hazard)
                VALUES (:id,:request_id,:token_raw,:candidate_canonical_name,:match_score,:was_accepted,:is_hazard)
                ON CONFLICT (review_id) DO NOTHING RETURNING review_id
            """), dict(id=identity, request_id=str(request_id), **item))
            count += int(inserted is not None)
    return count


async def process_ocr_result(session, result, preferences, request_id=None):
    from app.services.analysis_service import AnalysisService
    result.request_id = str(request_id or result.request_id or uuid4())
    resolver = OcrIngredientResolver(session)
    analysis = None
    if result.status=="success" and result.composition_text:
        analysis = await AnalysisService(session, resolver=resolver).analyze(result.composition_text, preferences)
    result.flagged_hazards = await catalog_hazards(session, result.raw_text or "", result.confidence)
    candidates = resolver.review_candidates + [
        dict(token_raw=f.token_raw, candidate_canonical_name=f.name, match_score=0.5,
             was_accepted=False, is_hazard=True)
        for f in result.flagged_hazards if f.certainty=="needs_verification"]
    try:
        result.queued_for_review = await enqueue_reviews(result.request_id, candidates)
    except Exception as exc:
        # A queue outage must not discard recognised text or warnings.
        result.review_queue_status = "unavailable"
        log.warning("OCR review queue unavailable request_id=%s error_type=%s", result.request_id, type(exc).__name__)
    log.info("OCR catalog request_id=%s matched=%s unmatched=%s hazards=%s queued=%s",
             result.request_id, len(analysis.matched) if analysis else 0,
             len(analysis.unmatched) if analysis else 0, len(result.flagged_hazards), result.queued_for_review)
    return analysis
