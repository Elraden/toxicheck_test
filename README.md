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

## Deploy Backend To Render

Render deployment files:

- `render.yaml`
- `backend/Dockerfile.render`

The Render image installs system Tesseract and runs OCR inside the backend with:

```env
OCR_MODE=embedded
```

After Render creates the service, copy its public URL and set this variable in
the Vercel frontend project:

```env
VITE_API_BASE_URL=https://your-render-service.onrender.com/api
```

Then redeploy Vercel.
