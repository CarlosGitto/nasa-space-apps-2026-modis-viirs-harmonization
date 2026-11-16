"""FastAPI app: fire detections from the FIRMS lake for the frontend."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import router


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["X-Truncated", "Content-Disposition"],  # the hotspots CSV download
)

app.include_router(router)
