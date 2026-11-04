"""Research sessions: server-owned snapshots and explicit stage transitions."""
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.research import RunInput, StageInput, ValidationInput


def utcnow():
    return datetime.now(timezone.utc)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def normalize_codes(codes: list[str]) -> list[str]:
    result = set()
    for code in codes:
        code = re.sub(r'[\s-]', '', code).upper().replace('Е', 'E')
        if not re.fullmatch(r'E\d{3,4}[A-Z]?(?:\([IVX]+\))?', code):
            raise HTTPException(422, f'Некорректный E-код: {code[:40]}')
        result.add(code)
    return sorted(result)


def transition(run: dict, payload: StageInput, now: datetime) -> dict:
    """Pure transition rules; timings are server wall-clock durations, never client claims."""
    if run['revision'] != payload.revision:
        raise HTTPException(409, 'Этап изменился. Обновите проверку перед сохранением.')
    stage, action = run['stage'], payload.action
    valid = {
        'start_manual': 'manual_ready', 'save_manual': 'manual_active', 'finish_manual': 'manual_active',
        'start_app': 'app_ready', 'save_app': 'app_active', 'finish_app': 'app_active', 'annotate': 'labeling',
    }
    if action == 'abort':
        if stage in ('completed', 'aborted'):
            raise HTTPException(409, 'Проверка уже завершена.')
        return {'stage': 'aborted', 'error_reason': payload.reason or 'Прервано участником'}
    if valid.get(action) != stage:
        raise HTTPException(409, 'Этот шаг сейчас недоступен.')
    if action.startswith(('save_', 'finish_')):
        keys = {item['e_code'] for item in run['exclusions']}
        if not set(payload.answers).issubset(keys):
            raise HTTPException(422, 'Ответы содержат добавки вне условий проверки.')
        if action.startswith('finish_') and set(payload.answers) != keys:
            raise HTTPException(422, 'Выберите ответ для каждой добавки, включая «Не удалось определить».')
        method = 'manual' if action.endswith('manual') else 'app'
        changes = {f'{method}_answers': payload.answers}
        if method == 'manual':
            changes['external_search_used'] = payload.external_search_used
        if action.startswith('finish_'):
            if method == 'manual' and payload.external_search_used is None:
                raise HTTPException(422, 'Укажите, использовали ли поиск в интернете.')
            changes.update({f'{method}_finished_at': now,
                            f'{method}_duration_ms': max(0, round((now - run[f'{method}_started_at']).total_seconds() * 1000)),
                            'stage': 'app_ready' if method == 'manual' else 'labeling'})
            if method == 'app':
                changes['selected_attempt_id'] = payload.selected_attempt_id
        return changes
    if action == 'start_manual':
        return {'stage': 'manual_active', 'manual_started_at': now}
    if action == 'start_app':
        return {'stage': 'app_active', 'app_started_at': now}
    if not payload.skip_label and not payload.user_label_text.strip():
        raise HTTPException(422, 'Введите состав или выберите пропуск разметки.')
    return {'stage': 'completed', 'labeling_status': 'skipped' if payload.skip_label else 'done',
            'user_label_text': None if payload.skip_label else payload.user_label_text.strip()}


class ResearchService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def audit(self, actor, event, run_id=None, details=None):
        await self.db.execute(text('''INSERT INTO research.audit_events(actor_id,event,run_id,details)
            VALUES(:actor,:event,:run,CAST(:details AS jsonb))'''),
            dict(actor=actor, event=event, run=run_id, details=encoded(details or {})))

    async def owned(self, run_id, user_id, lock=False):
        row = (await self.db.execute(text('''SELECT r.* FROM research.runs r
            JOIN research.participants p ON p.id=r.participant_id
            WHERE r.id=:id AND p.user_id=:user''' + (' FOR UPDATE OF r' if lock else '')),
            dict(id=run_id, user=user_id))).mappings().first()
        if not row:
            raise HTTPException(404, 'Проверка не найдена.')
        return dict(row)

    async def detail(self, run_id, user_id=None, admin=False, reveal=False):
        if not admin:
            row = await self.owned(run_id, user_id)
        else:
            record = (await self.db.execute(text('''SELECT r.*, c.title AS case_title, s.title AS study_title
                FROM research.runs r JOIN research.cases c ON c.id=r.case_id
                JOIN research.studies s ON s.id=r.study_id WHERE r.id=:id'''), dict(id=run_id))).mappings().first()
            if not record:
                raise HTTPException(404, 'Проверка не найдена.')
            row = dict(record)
        row['photos'] = [dict(item) for item in (await self.db.execute(text('''
            SELECT id,mime_type,sha256,size_bytes,uploaded_at FROM research.photos WHERE run_id=:id ORDER BY uploaded_at
        '''), dict(id=run_id))).mappings()]
        attempts = [dict(item) for item in (await self.db.execute(text('''
            SELECT * FROM research.attempts WHERE run_id=:id ORDER BY started_at,id
        '''), dict(id=run_id))).mappings()]
        if admin and not reveal:
            for attempt in attempts:
                attempt.pop('result', None)
            row.pop('manual_answers', None)
            row.pop('app_answers', None)
        row['attempts'] = attempts
        if admin:
            validation = (await self.db.execute(text('SELECT * FROM research.validations WHERE run_id=:id'), dict(id=run_id))).mappings().first()
            row['validation'] = dict(validation) if validation else None
            row['audit'] = [dict(item) for item in (await self.db.execute(text('''
                SELECT * FROM research.audit_events WHERE run_id=:id ORDER BY created_at,id
            '''), dict(id=run_id))).mappings()]
        return row

    async def create(self, user_id, payload: RunInput):
        # Serialize concurrent creation/import against the same profile.
        await self.db.execute(text('SELECT id FROM public.users WHERE id=:id FOR UPDATE'), dict(id=user_id))
        previous = await self.db.scalar(text('SELECT id FROM research.runs WHERE id=:id'), dict(id=payload.id))
        if previous:
            row = await self.owned(payload.id, user_id)
            if row['study_id'] != payload.study_id:
                raise HTTPException(409, 'Идентификатор проверки уже использован.')
            return await self.detail(payload.id, user_id)
        study = (await self.db.execute(text('SELECT * FROM research.studies WHERE id=:id FOR SHARE'), dict(id=payload.study_id))).mappings().first()
        if not study or not study['is_open']:
            raise HTTPException(409, 'Исследование закрыто для новых проверок.')
        exclusions = [dict(item) for item in (await self.db.execute(text('''
            SELECT i.id::text AS ingredient_id, upper(i.e_code) AS e_code
            FROM public.user_ingredient_exclusions x JOIN catalog.ingredients i ON i.id=x.ingredient_id
            WHERE x.user_id=:user AND i.is_active AND i.e_code IS NOT NULL ORDER BY i.e_code
        '''), dict(user=user_id))).mappings()]
        if study['scenario'] == 'comparison' and not exclusions:
            raise HTTPException(422, 'Сначала выберите хотя бы одну E-добавку в исключениях профиля.')
        await self.db.execute(text('INSERT INTO research.participants(id,user_id) VALUES(:id,:user) ON CONFLICT(user_id) DO NOTHING'),
                              dict(id=uuid4(), user=user_id))
        participant = await self.db.scalar(text('SELECT id FROM research.participants WHERE user_id=:user'), dict(user=user_id))
        case_id = uuid4()
        await self.db.execute(text('INSERT INTO research.cases(id,title) VALUES(:id,:title)'), dict(id=case_id, title=payload.title))
        await self.db.execute(text('''INSERT INTO research.runs
            (id,study_id,participant_id,case_id,scenario,protocol_version,instruction,consent_version,exclusions,stage)
            VALUES(:id,:study,:participant,:case,:scenario,:version,:instruction,'research-consent-v1',CAST(:exclusions AS jsonb),:stage)
        '''), dict(id=payload.id, study=payload.study_id, participant=participant, case=case_id,
                   scenario=study['scenario'], version=study['protocol_version'], instruction=study['instruction'],
                   exclusions=encoded(exclusions), stage='manual_ready' if study['scenario'] == 'comparison' else 'app_ready'))
        await self.db.execute(text('INSERT INTO research.validations(run_id) VALUES(:id)'), dict(id=payload.id))
        await self.audit(user_id, 'run_created', payload.id)
        return await self.detail(payload.id, user_id)

    async def change_stage(self, user_id, run_id, payload: StageInput):
        row = await self.owned(run_id, user_id, lock=True)
        changes = transition(row, payload, utcnow())
        if payload.action == 'finish_app':
            if not payload.selected_attempt_id:
                raise HTTPException(422, 'Выберите попытку анализа, по которой отвечаете.')
            attempt = (await self.db.execute(text('SELECT * FROM research.attempts WHERE id=:id AND run_id=:run'),
                                            dict(id=payload.selected_attempt_id, run=run_id))).mappings().first()
            if not attempt or attempt['status'] == 'processing' or not attempt['displayed_at']:
                raise HTTPException(422, 'Сначала дождитесь отображения результата анализа.')
        if payload.action == 'abort':
            await self.db.execute(text("UPDATE research.attempts SET status='interrupted',error='Проверка прервана',finished_at=now() WHERE run_id=:id AND status='processing'"), dict(id=run_id))
        params = {'id': run_id}
        setters = []
        for field, value in changes.items():
            is_json = field.endswith('_answers')
            params[field] = encoded(value) if is_json else value
            setters.append(f'{field}=CAST(:{field} AS jsonb)' if is_json else f'{field}=:{field}')
        await self.db.execute(text('UPDATE research.runs SET ' + ','.join(setters) + ',revision=revision+1,updated_at=now() WHERE id=:id'), params)
        await self.audit(user_id, payload.action, run_id)
        return await self.detail(run_id, user_id)

    async def validate(self, actor, run_id, payload: ValidationInput):
        run = (await self.db.execute(text('SELECT * FROM research.runs WHERE id=:id FOR UPDATE'), dict(id=run_id))).mappings().first()
        if not run:
            raise HTTPException(404, 'Проверка не найдена.')
        current = (await self.db.execute(text('SELECT * FROM research.validations WHERE run_id=:id FOR UPDATE'), dict(id=run_id))).mappings().one()
        if current['revision'] != payload.revision:
            raise HTTPException(409, 'Эталон изменён другим проверяющим. Обновите страницу.')
        codes = normalize_codes(payload.ground_truth_e_codes)
        if payload.status == 'confirmed':
            if run['stage'] not in ('completed', 'aborted'):
                raise HTTPException(409, 'Подтверждение доступно после завершения участником проверки.')
            if not payload.verified_label_text.strip() or not await self.db.scalar(text('SELECT count(*) FROM research.photos WHERE run_id=:id'), dict(id=run_id)):
                raise HTTPException(422, 'Для подтверждения нужны фотография и проверенный текст состава.')
        await self.db.execute(text('''UPDATE research.validations SET revision=revision+1,status=:status,
            verified_label_text=:label,ground_truth_e_codes=CAST(:codes AS jsonb),comment=:comment,
            reviewer_id=:actor,updated_at=now() WHERE run_id=:id'''),
            dict(id=run_id, status=payload.status, label=payload.verified_label_text.strip(), codes=encoded(codes),
                 comment=payload.comment, actor=actor))
        await self.audit(actor, 'validation_updated', run_id, {'before': dict(current), 'after': payload.model_dump()})
        return await self.detail(run_id, admin=True)


async def catalog_fingerprint(db: AsyncSession) -> str:
    # Catalog schema version alone is not a content revision. Hash the actual input tables.
    tables = ('ingredients', 'ingredient_aliases', 'ingredient_alias_review', 'ingredient_matches',
              'ingredient_rules', 'regulatory_sources', 'rule_evidence', 'rule_transitions')
    digests = []
    for table in tables:
        digest = await db.scalar(text(f'''SELECT md5(COALESCE(string_agg(h, '' ORDER BY h), ''))
            FROM (SELECT md5(row_to_json(t)::text) AS h FROM catalog.{table} t) rows'''))
        digests.append(digest)
    return 'sha256:' + hashlib.sha256('|'.join(digests).encode()).hexdigest()
