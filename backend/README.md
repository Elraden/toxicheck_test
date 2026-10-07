# ToxiCheck Backend

Backend foundation for ingredient recognition and risk rule evaluation.

## Architecture Choice

Start as a modular monolith:

- `ingredients`: canonical ingredient/additive directory.
- `aliases`: synonyms, spelling variants, English/Russian names, E-code variants.
- `rules`: regulatory and product safety rules.
- `recognition`: text normalization and ingredient matching.
- `ocr`: integration boundary for future OCR/neural model processing.

OCR runs in embedded mode on Railway: `app/services/ocr_engine.py` performs
adaptive preprocessing and Tesseract recognition inside the main API process.
The existing catalog resolver then reads ingredients, aliases and rules via
`TOXICHECK_CATALOG_DATABASE_URL`. No prototype database or `decision_tree` tables
are required. Low-confidence photos return `needs_retake` instead of a verdict.
HTTP mode remains an optional adapter for a separately hosted OCR engine.

See the diagram in `../docs/backend-architecture.md`.

## Local Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate
pip install -e .
uvicorn app.main:app --reload
```

Set `TOXICHECK_CATALOG_DATABASE_URL` in the ignored `backend/.env` to the populated
catalog database. API reads use schema `catalog`, version 2, in read-only mode.
An empty catalog setting falls back to `DATABASE_URL`. Existing legacy tables
in `public` are not a substitute for the new schema. Do not apply `db/schema.sql`
to the catalog database. Future application storage can use its own `DATABASE_URL`.

## Catalog Data Import

Build and validate the local catalog without accessing a database:

Catalog input/export snapshots are local artifacts, not part of the deployment.
Supply the edited input dataset and OFF snapshot locally before rebuilding;
the deployed API reads the already populated database and needs neither file.

```bash
cd backend
python -m app.db.build_catalog
python -m app.db.import_catalog --validate-only
```

Only when a database update is intended, import from the same directory:

```bash
python -m app.db.import_catalog --prompt-url
```

The importer applies `db/schema_catalog.sql` and replaces the catalog snapshot
transactionally. `--replace-data` explicitly truncates data tables first, in
the same transaction, retaining import history. It uses the dedicated catalog
URL, not `DATABASE_URL`. The current remote catalog is already populated.
See `../README_catalog_railway.md` for provenance and review caveats.

For Railway, set this reference variable on the backend service (replace the
service name if different):

```env
TOXICHECK_CATALOG_DATABASE_URL=${{Postgres-hcbk.DATABASE_URL}}
```

Redeploy the updated backend. Build/startup no longer import legacy data.
Remove any manually configured old pre-deploy import command as well.
Check `/api/health/db`: HTTP 200, schema `catalog`, schema version 2.
The liveness endpoint `/api/health` does not depend on database availability.
Local clients need the public database URL, while Railway uses its private URL.

## OCR/ML Service

Embedded mode keeps OCR inside the backend process:

```env
OCR_MODE=embedded
OCR_SERVICE_URL=
```

Install OCR Python dependencies and system Tesseract on the backend host:

```bash
cd backend
pip install -e ".[ocr]"
```

HTTP mode runs the ML specialist's OCR service on a separate port:

```bash
cd path/to/ocr_service_directory
uvicorn ocr_service:app --host 0.0.0.0 --port 8001
```

Configure the backend:

```env
OCR_MODE=http
OCR_SERVICE_URL=http://localhost:8001
```

If the backend is running from `docker-compose.backend.yml` in HTTP mode, it
calls the host OCR service through:

```env
OCR_SERVICE_URL=http://host.docker.internal:8001
```

The backend expects OCR to produce `extracted_composition`. If the model cannot
extract composition, the result page still opens and shows "Состав не
определен".

## API

- `GET /api/health`
- `GET /api/health/db`
- `POST /api/ingredients/resolve`
- `POST /api/analysis`
- `POST /api/scan/barcode`
- `POST /api/scan/composition`
- `GET /api/scan/jobs/{job_id}`
- `GET /api/preferences/catalog`
- `POST /api/preferences/anonymous`
- `POST /api/compare`
- `GET /api/auth/state`
- `GET /api/billing/state`

## Module Boundaries

```text
api/routes
  HTTP contracts only

services/open_food_facts.py
  External product lookup by barcode

services/ingredient_resolver.py
  Batched exact E-code and unique normalized alias matching in catalog v2

services/verdict_engine.py
  Verdict and reasons; missing rules remain unknown, not safe

services/analysis_service.py
  Resolve ingredients and build a verdict

services/scan_service.py
  Barcode flow orchestration

services/ocr_service.py
  Adapter for embedded OCR and remote HTTP OCR modes

services/feature_gate.py
  Premium feature checks for history, compare, and future account features
```

## Data Ownership

The backend does not store a product catalog. It stores:

- ingredient directory;
- ingredient aliases and synonyms;
- regulatory and risk rules;
- future users and premium subscriptions;
- future saved scan results for premium users only.

Open Food Facts data is used as an input source. For premium history, save only
the final analysis snapshot and raw ingredient text.

## Future Split Points

Keep these in the monolith for now:

- barcode scan orchestration;
- ingredient resolver;
- rules engine;
- preferences;
- compare;
- billing routes.

Can stay embedded or be separated:

- OCR/ML service, because it can need GPU, separate scaling, and stricter
  failure isolation.

Example:

```json
{
  "ingredientsText": "сахар, диоксид титана E171, лимонная кислота"
}
```
