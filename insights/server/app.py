"""FastAPI app factory. The lifespan starts the scheduler and the live-log hub."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from insights.config import Config, load_config
from insights.core.llm import LLMClient, OpenAILLM
from insights.db.session import make_engine, make_session_factory
from insights.jobs import builtin  # noqa: F401  (registers built-in jobs)
from insights.jobs.broadcast import BroadcastHub, set_hub
from insights.jobs.registry import mark_interrupted_runs, registry
from insights.jobs.scheduler import build_scheduler
from insights.server.api import router as api_router
from insights.server.gui import router as gui_router
from insights.server.gui_pages import router as gui_pages_router

STATIC_DIR = Path(__file__).parent / "static"


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: Config = app.state.config
    app.state.broadcast_hub.attach(asyncio.get_running_loop())
    set_hub(app.state.broadcast_hub)
    with app.state.session_factory() as session:
        mark_interrupted_runs(session)
    scheduler = build_scheduler(config, registry, app.state.session_factory, app.state.llm)
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)
    set_hub(None)


def create_app(config: Config | None = None, llm: LLMClient | None = None) -> FastAPI:
    resolved = config or load_config()
    app = FastAPI(title="work-insights", lifespan=_lifespan)
    app.state.config = resolved
    app.state.session_factory = make_session_factory(make_engine(resolved))
    app.state.llm = llm or OpenAILLM(resolved.llm)
    app.state.job_registry = registry
    app.state.broadcast_hub = BroadcastHub()
    app.include_router(api_router)
    app.include_router(gui_router)
    app.include_router(gui_pages_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
