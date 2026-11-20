"""FastAPI app: fire detections from the FIRMS lake for the frontend."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app import db
from app.api import router


class ReattachOnStaleCatalog:
    """The ETL rewrites tables under a running backend. When a GET fails for that reason before anything was sent,
    re-attach the lake and run it once more (a plain ASGI middleware: it can call the app again, call_next cannot)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET":
            return await self.app(scope, receive, send)
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            if started or not db.is_stale_error(exc) or not db.refresh():
                raise
            await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.connect()
    yield
    db.close()


app = FastAPI(
    title="FIRMS fire detections API",
    version="0.1.0",
    description="MODIS, VIIRS and (soon) harmonized fire detections read from the data/lake gold layer.",
    lifespan=lifespan,
)

app.add_middleware(ReattachOnStaleCatalog)
# JSON / GeoJSON / CSV compress 5-10x; without this every map move downloads megabytes. Inside CORS (which stays outermost).
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["X-Truncated", "Content-Disposition"],  # the hotspots CSV download
)

app.include_router(router)
