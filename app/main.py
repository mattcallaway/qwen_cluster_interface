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
    yield
    # Shutdown sequence
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
app.include_router(settings_route.router)

if __name__ == "__main__":
    import uvicorn
    is_dev = settings.environment == "development"
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=is_dev)
