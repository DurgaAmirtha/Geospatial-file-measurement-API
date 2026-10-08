import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import Base, engine
from app.routers import files

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description="Upload a KML or zipped Shapefile and get per-feature area/length measurements.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
