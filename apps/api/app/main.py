"""FastAPI application for Katalog Kilat.

Scope of this module (live probe): the application object and a single route,
``GET /api/health``, which reports process liveness and the mock flag.

The health route imports, calls and probes nothing belonging to Meta. It
proves the process is up and which mode it runs in, nothing more.
"""

from fastapi import FastAPI

from app.settings import settings

app = FastAPI(title="Katalog Kilat API")


@app.get("/api/health")
async def health() -> dict[str, bool]:
    """Report process liveness and whether the service runs in mock mode."""
    return {"ok": True, "mock": settings.meta_mock}
