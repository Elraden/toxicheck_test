# ToxiCheck Backend

Backend foundation for ingredient recognition and risk rule evaluation.

## Architecture Choice

Start as a modular monolith:

- `ingredients`: canonical ingredient/additive directory.
- `aliases`: synonyms, spelling variants, English/Russian names, E-code variants.
- `rules`: regulatory and product safety rules.
- `recognition`: text normalization and ingredient matching.
- `ocr`: integration boundary for future OCR/neural model processing.

The OCR/ML model can run in two modes. For local monolith-style development,
use embedded mode: the backend imports `../ocr_service.py` and runs recognition
inside the main API process. For production or heavier ML runtime, use HTTP
mode: the backend calls a separate OCR service URL.

See the diagram in `../docs/backend-architecture.md`.

## Local Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate
pip install -e .
uvicorn app.main:app --reload
```

Apply the PostgreSQL schema from `db/schema.sql` before using resolve endpoints.

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
  E-code, exact alias, and pg_trgm similarity matching

services/verdict_engine.py
  Product risk score, verdict level, and explanation reasons

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
