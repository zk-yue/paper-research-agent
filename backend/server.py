"""Server entry point; also supports the legacy `python backend/server.py`."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app
from settings import ServerSettings

app = create_app()


def main():
    import uvicorn

    settings = ServerSettings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
