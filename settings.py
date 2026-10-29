"""Application paths and server settings, independent of the working directory."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_ROOT = (
    PROJECT_ROOT
    if (PROJECT_ROOT / "pyproject.toml").is_file()
    else Path.home() / ".local" / "share" / "paper-research-agent"
)
load_dotenv(DEFAULT_DATA_ROOT / ".env")
DATA_ROOT = (
    Path(os.getenv("PAPER_RESEARCH_DATA_DIR", str(DEFAULT_DATA_ROOT))).expanduser().resolve()
)


@dataclass(frozen=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    @classmethod
    def from_env(cls):
        port = int(os.getenv("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        origins = os.getenv("CORS_ORIGINS", ",".join(cls.cors_origins))
        return cls(
            host=os.getenv("HOST", "127.0.0.1"),
            port=port,
            cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        )
