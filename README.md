# spend-mind
AI-powered personal expense tracking and analytics assistant.

## Local backend setup

From the repository root, copy `.env.example` to `.env`. The example values are for local development. If port 5432 is occupied, set `POSTGRES_PORT` in `.env` to an available port.

```sh
uv sync --locked
docker compose up -d --wait db
uv run python -m alembic upgrade head
uv run python -m uvicorn spend_mind.main:app --reload
```

`GET http://127.0.0.1:8000/health` reports API liveness. Check PostgreSQL readiness with `docker compose ps`.

The API and Alembic use the same PostgreSQL settings. The migration history starts empty, and pgvector activation is deferred until a schema migration needs it.

Run the checks with:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
