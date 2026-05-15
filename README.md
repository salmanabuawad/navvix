# Navvix

Production-oriented React/TypeScript + Python/FastAPI + PostgreSQL codebase for DXF architectural dimensioning.

## Stack

- Frontend: React + TypeScript + Vite + Tailwind
- Backend: Python + FastAPI
- Database: PostgreSQL via SQLAlchemy
- CAD engine: Python + ezdxf + matplotlib preview rendering

## Core principle

The engine must not dimension raw CAD lines directly.

Pipeline:

1. Isolate the main architectural plan.
2. Reject page frames, title blocks, legends, schedules, tables, and notes.
3. Extract a normalized line registry.
4. Build local/topological wall groups.
5. Generate semantic dimension candidates with ownership.
6. Apply perimeter-first and space-priority strategy.
7. Render preview and validate output quality.

See:

- `docs/CLAUDE_DO_NOT_OVERWRITE.md`
- `docs/ENGINE_RULES.md`
- `prompts/CLAUDE_UPDATE_PROMPT.md`

## Run backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Run frontend

```bash
cd frontend
npm install
npm run dev
```

## PostgreSQL

Use the included `docker-compose.yml`:

```bash
docker compose up -d postgres
```
