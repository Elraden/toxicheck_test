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

In production, point the frontend to the Render backend with:

```env
VITE_API_BASE_URL=https://your-render-service.onrender.com/api
```

## Backend

Backend architecture and API foundation live in `backend/`.

- Architecture diagram: `docs/backend-architecture.svg`
- Architecture notes: `docs/backend-architecture.md`
- Backend details: `backend/README.md`

Run backend stack:

```sh
docker compose -f docker-compose.backend.yml up --build
```

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
```

After Railway creates a public backend domain, copy its URL and set this variable in
the Vercel frontend project:

```env
VITE_API_BASE_URL=https://your-railway-service.up.railway.app/api
```

Then redeploy Vercel.

The Docker build creates the regulatory bundle from the two root JSON inputs
and the seeds in `backend/db`. Railway's pre-deploy command imports it into
the database specified by the backend's `DATABASE_URL`, in one transaction.
Repeated imports update stable IDs without duplicating records. No local
PostgreSQL is required. Use the private Railway variable reference above for
the deployed backend; the public database URL is only for external imports.

Check `/api/health/db` after deployment: it reports database availability and
catalog row counts. The result page calls `/api/analysis` for barcode and OCR
compositions, and preferences load `/api/preferences/catalog`. Neither screen
uses a local risk catalog. Unknown ingredients remain visible as unknown.

Vercel's `frontend/vercel.json` proxies `/api/*` to the Railway backend, so
`VITE_API_BASE_URL` can be omitted in production for this deployment. Update
the proxy destination when changing the backend domain.
