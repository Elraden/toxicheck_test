# ToxiCheck

Monorepo structure:

```text
frontend/  Vue + Vite mobile web app
backend/   FastAPI backend foundation
docs/      architecture diagrams and notes
```

## Frontend

```sh
cd frontend
npm install
npm run dev
```

Build:

```sh
cd frontend
npm run build
```

Deploy frontend to Vercel:

```sh
cd frontend
npx vercel --prod --scope toxi-check
```

In production, point the frontend to the Railway backend with:

```env
VITE_API_BASE_URL=https://your-railway-service.up.railway.app/api
```

## Backend

Backend architecture and API foundation live in `backend/`.

- Architecture diagram: `docs/backend-architecture.svg`
- Architecture notes: `docs/backend-architecture.md`
- Backend details: `backend/README.md`

Run against the existing catalog (no local database required). Set
`TOXICHECK_CATALOG_DATABASE_URL` in ignored `backend/.env`, then:

```sh
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

An optional local backend stack is also available:

```sh
docker compose -f docker-compose.backend.yml up --build
```

A new local Postgres volume creates schema `catalog`, but starts empty. Import
the catalog explicitly before analysis; an existing volume is not migrated
by Docker initialization scripts. Do not delete a volume to switch catalogs.

Backend API will be available at:

```text
http://localhost:8000/api
```

## Deploy Backend To Railway

Railway deployment files:

- `railway.json`
- `backend/Dockerfile.railway`

The Railway image installs system Tesseract and runs OCR inside the backend with:

```env
OCR_MODE=embedded
```

Create a Railway project from the GitHub repository, add a Postgres service,
then set backend service variables:

```env
APP_NAME=ToxiCheck API
APP_ENV=production
FRONTEND_ORIGINS=https://toxicheck-ruby.vercel.app
OPEN_FOOD_FACTS_BASE_URL=https://world.openfoodfacts.org
OCR_MODE=embedded
OCR_SERVICE_URL=
DATABASE_URL=${{Postgres.DATABASE_URL}}
TOXICHECK_CATALOG_DATABASE_URL=${{Postgres-hcbk.DATABASE_URL}}
```

After Railway creates a public backend domain, copy its URL and set this variable in
the Vercel frontend project:

```env
VITE_API_BASE_URL=https://your-railway-service.up.railway.app/api
```

Then redeploy Vercel.

The API reads the already imported `catalog` schema, version 2, using a separate
read-only connection. `TOXICHECK_CATALOG_DATABASE_URL` takes priority; if empty,
the API uses `DATABASE_URL`, which must then point to the catalog database.
Keep a separate `DATABASE_URL` for future application storage if needed.
The Docker build and Railway pre-deploy no longer build or import legacy data.
Remove any old pre-deploy command also configured manually in Railway Settings.
Use the private database reference for Railway; a local API or importer needs
the public database URL. No URL or credentials belong in frontend variables.
See `README_catalog_railway.md` for explicit catalog imports.

Check `/api/health/db` after deployment: it reports database availability and
catalog row counts. The result page calls `/api/analysis` for barcode and OCR
compositions, and preferences load `/api/preferences/catalog`. Neither screen
uses a local risk catalog. Unknown ingredients remain visible as unknown.
Found ingredients without rules have severity `unknown`, not a safety rating.
Matching uses exact E-codes (including subtypes) and unique normalized aliases;
ambiguous aliases and fuzzy matches cannot silently choose an ingredient.

Read-only integration check against the configured catalog, in PowerShell:

```powershell
cd backend
$env:TOXICHECK_DB_SMOKE = '1'
python -m unittest discover -s tests -p test_database_smoke.py
Remove-Item Env:TOXICHECK_DB_SMOKE
```

Vercel's `frontend/vercel.json` proxies `/api/*` to the Railway backend, so
`VITE_API_BASE_URL` can be omitted in production for this deployment. Update
the proxy destination when changing the backend domain.
