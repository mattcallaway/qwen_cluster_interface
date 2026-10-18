import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Automatically load production or user config if present
_custom_config = os.getenv("QC_CONFIG_FILE")
if _custom_config and Path(_custom_config).exists():
    load_dotenv(dotenv_path=Path(_custom_config))
_persistent_env = Path.home() / ".qwen-collective" / "qwen-collective.env"
if _persistent_env.exists():
    load_dotenv(dotenv_path=_persistent_env)
load_dotenv()


class Settings(BaseModel):
    app_name: str = "Qwen Collective"
    app_version: str = "0.1.0"
    host: str = os.getenv("QC_HOST", "127.0.0.1")
    port: int = int(os.getenv("QC_PORT", "8000"))
    
    # Base data directory
    data_dir: Path = Path(os.getenv("QC_DATA_DIR", "./data")).resolve()
    
    # Security
    secret_key: str = os.getenv("QC_SECRET_KEY", "qwen-collective-insecure-secret-key-change-me")
    admin_password: str = os.getenv("QC_ADMIN_PASSWORD", "admin123")
    session_expire_hours: int = int(os.getenv("QC_SESSION_EXPIRE_HOURS", "72"))
    
    environment: str = os.getenv("QC_ENV", "development").lower()

    # Project Orchestrator background engine (opt-in; the UI/API are always available, the engine loop only runs when enabled)
    orchestrator_enabled: bool = os.getenv("QC_ORCHESTRATOR_ENABLED", "0").lower() in ("1", "true", "yes")
    
    # Paths derived from data_dir
    @property
    def db_path(self) -> Path:
        return self.data_dir / "qwen_collective.db"
        
    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    @property
    def evidence_dir(self) -> Path:
        p = self.data_dir / "evidence"
        p.mkdir(parents=True, exist_ok=True)
        return p

settings = Settings()

def validate_production_settings():
    """
    Enforce production security rules:
    - Default administrator password is strictly rejected in production.
    - Default or short session secret key is strictly rejected in production.
    """
    if settings.environment == "production":
        insecure_secrets = [
            "qwen-collective-insecure-secret-key-change-me",
            "replace-with-a-random-secret-key-in-production",
            "secret",
            "changeme",
            ""
        ]
        if settings.secret_key in insecure_secrets or len(settings.secret_key) < 32:
            raise RuntimeError(
                "PRODUCTION CONFIGURATION ERROR: A unique, strong QC_SECRET_KEY (minimum 32 characters) "
                "is strictly required in production mode."
            )
            
        insecure_passwords = [
            "admin123",
            "change-me-promptly",
            "admin",
            "password",
            "123456",
            ""
        ]
        if settings.admin_password in insecure_passwords or len(settings.admin_password) < 12:
            raise RuntimeError(
                "PRODUCTION CONFIGURATION ERROR: The default or weak administrator password is not allowed "
                "in production. Set a unique QC_ADMIN_PASSWORD of at least 12 characters."
            )

def ensure_directories():
    """Ensure data and knowledge directories exist on startup."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
    
    subdirs = [
        "system",
        "projects",
        "research",
        "sources",
        "decisions",
        "journal",
        "inbox",
    ]
    for sub in subdirs:
        (settings.knowledge_dir / sub).mkdir(parents=True, exist_ok=True)
