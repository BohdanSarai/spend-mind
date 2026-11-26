from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from spend_mind.config import Settings
from spend_mind.routers.users import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        app.state.settings = settings
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        yield
    finally:
        await engine.dispose()


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


app = FastAPI(title="SpendMind", lifespan=lifespan)
app.include_router(users_router)


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()
