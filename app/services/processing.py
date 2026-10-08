"""Orchestrates: parse file -> measure each feature -> persist."""
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Feature, UploadedFile
from app.services.crs import crs_label
from app.services.measurements import measure_geometry
from app.services.parser import ProcessingError, parse_kml, parse_shapefile_zip

log = logging.getLogger(__name__)


def process_upload(db: Session, record: UploadedFile, saved_path: Path, workdir: Path, assume_crs: str | None) -> None:
    """Fill `record` in place. Raises ProcessingError after marking the record FAILED."""
    try:
        if record.file_type == "kml":
            parsed = parse_kml(saved_path, assume_crs)
        else:
            parsed = parse_shapefile_zip(saved_path, workdir, assume_crs)
        if not parsed:
            raise ProcessingError("No features were found in the file.")

        rows = [
            Feature(
                file_id=record.id,
                feature_index=pf.index,
                layer=pf.layer,
                geometry_type=pf.geometry.geom_type if pf.geometry is not None else "None",
                crs=crs_label(pf.crs),
                geometry=pf.geojson,
                properties=pf.properties,
                measurement=measure_geometry(pf.geometry, pf.crs),
            )
            for pf in parsed
        ]
        labels = {r.crs for r in rows}
        record.crs = labels.pop() if len(labels) == 1 else "MIXED"
        record.feature_count = len(rows)
        record.status = "COMPLETED"
        db.add_all(rows)
        db.add(record)
        db.commit()
    except ProcessingError as exc:
        _mark_failed(db, record, str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected error while processing %s", record.id)
        _mark_failed(db, record, "Unexpected error while processing the file.")
        raise ProcessingError("Unexpected error while processing the file.") from exc


def _mark_failed(db: Session, record: UploadedFile, message: str) -> None:
    db.rollback()
    record.status = "FAILED"
    record.error_message = message
    db.add(record)
    db.commit()
