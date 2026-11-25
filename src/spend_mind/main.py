from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from spend_mind.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    try:
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        yield
    finally:
        await engine.dispose()


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


app = FastAPI(title="SpendMind", lifespan=lifespan)


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()
