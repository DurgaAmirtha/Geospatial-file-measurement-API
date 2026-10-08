import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Feature, UploadedFile
from app.schemas import (
    FeatureMeasurement,
    FeatureOut,
    FeaturesResponse,
    FileInfo,
    MeasurementsResponse,
    MeasurementTotals,
)
from app.services.parser import ProcessingError
from app.services.processing import process_upload

router = APIRouter(prefix="/api/files", tags=["files"])

_KINDS = {".kml": "kml", ".zip": "shapefile"}
_CHUNK = 1024 * 1024


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


def _get_completed_or_409(db: Session, file_id: str) -> UploadedFile:
    record = _get_file_or_404(db, file_id)
    if record.status != "COMPLETED":
        detail = f"File is not ready (status={record.status}). {record.error_message or ''}".strip()
        raise HTTPException(status_code=409, detail=detail)
    return record


def _page(db: Session, file_id: str, limit: int, offset: int):
    return db.scalars(
        select(Feature).where(Feature.file_id == file_id).order_by(Feature.feature_index).offset(offset).limit(limit)
    ).all()


@router.post("/", response_model=FileInfo, status_code=201, summary="Upload a .kml or zipped Shapefile")
def upload_file(
    file: UploadFile = File(...),
    assume_crs: str | None = Query(
        None, description="CRS to assume if the file has none (e.g. Shapefile without .prj), e.g. EPSG:4326"
    ),
    db: Session = Depends(get_db),
):
    filename = os.path.basename(file.filename or "")
    ext = Path(filename).suffix.lower()
    if ext not in _KINDS:
        raise HTTPException(
            status_code=415, detail="Unsupported file type. Upload a .kml or a .zip containing a Shapefile."
        )

    with tempfile.TemporaryDirectory(prefix="geo_") as tmp:
        workdir = Path(tmp)
        saved = workdir / f"upload{ext}"
        size, limit = 0, config.MAX_UPLOAD_MB * 1024 * 1024
        with saved.open("wb") as out:
            while chunk := file.file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"File exceeds {config.MAX_UPLOAD_MB} MB limit.")
                out.write(chunk)

        record = UploadedFile(filename=filename, file_type=_KINDS[ext], status="PROCESSING")
        db.add(record)
        db.commit()
        try:
            process_upload(db, record, saved, workdir, assume_crs)
        except ProcessingError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc), "id": record.id, "status": "FAILED"})
    db.refresh(record)
    return record


@router.get("/{file_id}/", response_model=FileInfo, summary="File information")
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/features/", response_model=FeaturesResponse, summary="Features with geometry and attributes")
def get_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    record = _get_completed_or_409(db, file_id)
    results = [
        FeatureOut(
            feature_id=r.feature_index, layer=r.layer, geometry_type=r.geometry_type, crs=r.crs,
            geometry=r.geometry, properties=r.properties,
        )
        for r in _page(db, file_id, limit, offset)
    ]
    return FeaturesResponse(file_id=file_id, total=record.feature_count, limit=limit, offset=offset, results=results)


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse, summary="Per-feature measurements")
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    include_geometry: bool = Query(False),
    db: Session = Depends(get_db),
):
    record = _get_completed_or_409(db, file_id)

    # Totals cover the whole file, not just the requested page.
    all_m = db.execute(select(Feature.measurement).where(Feature.file_id == file_id)).scalars().all()
    measured = [m for m in all_m if m.get("status") == "MEASURED"]
    totals = MeasurementTotals(
        total_area_sq_m=round(sum(m.get("area_sq_m", 0.0) for m in measured), 4),
        total_length_m=round(sum(m.get("length_m", 0.0) for m in measured), 4),
        measured=len(measured),
        not_required=sum(1 for m in all_m if m.get("status") == "NOT_REQUIRED"),
        unsupported_or_error=sum(1 for m in all_m if m.get("status") in ("UNSUPPORTED", "ERROR")),
    )
    results = [
        FeatureMeasurement(
            feature_id=r.feature_index, layer=r.layer, geometry_type=r.geometry_type, crs=r.crs,
            measurement=r.measurement, geometry=r.geometry if include_geometry else None,
        )
        for r in _page(db, file_id, limit, offset)
    ]
    return MeasurementsResponse(
        file_id=file_id, total=record.feature_count, limit=limit, offset=offset, totals=totals, results=results
    )


@router.delete("/{file_id}/", status_code=204, summary="Delete a file and its features")
def delete_file(file_id: str, db: Session = Depends(get_db)):
    db.delete(_get_file_or_404(db, file_id))
    db.commit()
