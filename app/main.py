import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings, ensure_directories, validate_production_settings
from app.database import init_db
from app.auth import ensure_default_admin
from app.services.knowledge import seed_initial_knowledge, rebuild_knowledge_index
from app.routes import (
    auth,
    dashboard,
    machines,
    inference,
    chat,
    knowledge,
    tasks,
    projects,
    infrastructure,
    worker,
    orchestration,
    observatory,
    settings as settings_route
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("qwen_collective")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup sequence
    logger.info("Initializing Qwen Collective Control Server...")
    validate_production_settings()
    ensure_directories()
    init_db()
    ensure_default_admin()
    seed_initial_knowledge()
    rebuild_knowledge_index()
    logger.info(f"Control Server ready at http://{settings.host}:{settings.port}")
    if settings.orchestrator_enabled:
        from app.services.orchestration import engine as orch_engine
        orch_engine.start_background()
        logger.info("Project Orchestrator engine loop started.")
    else:
        logger.info("Project Orchestrator engine loop disabled (set QC_ORCHESTRATOR_ENABLED=1 to enable).")

    # Start System Observatory background engine (local sampling, rollups, alerts)
    from app.services.observatory import engine as obs_engine
    obs_engine.start_background()
    logger.info("System Observatory background telemetry engine started.")

    yield
    # Shutdown sequence
    obs_engine.stop_background()
    if settings.orchestrator_enabled:
        from app.services.orchestration import engine as orch_engine
        orch_engine.stop_background()
    logger.info("Shutting down Qwen Collective Control Server.")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan
)

# Mount static assets
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Register route modules
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(machines.router)
app.include_router(inference.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(orchestration.router)
app.include_router(infrastructure.router)
app.include_router(worker.router)
app.include_router(observatory.router)
app.include_router(settings_route.router)

if __name__ == "__main__":
    import os
    import uvicorn
    # Reload is strictly forbidden in production mode; requires explicit QC_DEV_RELOAD in development
    should_reload = False
    if settings.environment == "development" and os.getenv("QC_DEV_RELOAD", "0").lower() in ("1", "true"):
        should_reload = True
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=should_reload)
