"""Measurement calculation for a single geometry."""
import math

import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from app.services.crs import WGS84, crs_label, pick_projected_crs, reproject

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString"}
POINTS = {"Point", "MultiPoint"}

# Measurement statuses
MEASURED = "MEASURED"
NOT_REQUIRED = "NOT_REQUIRED"
UNSUPPORTED = "UNSUPPORTED"
ERROR = "ERROR"


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> dict:
    """Return a JSON-serialisable measurement dict. Never raises."""
    if geom is None or geom.is_empty:
        return {"status": UNSUPPORTED, "note": "Empty or missing geometry."}

    gtype = geom.geom_type
    if gtype in POINTS:
        return {"status": NOT_REQUIRED, "note": "No measurement is defined for points."}
    if gtype not in POLYGONAL | LINEAR:
        return {"status": UNSUPPORTED, "note": f"Measurement is not supported for {gtype}."}

    try:
        geom2d = shapely.force_2d(geom)  # KML often carries Z; we measure horizontally
        geom_ll = geom2d if src_crs.to_epsg() == 4326 else reproject(geom2d, src_crs, WGS84)
        target = pick_projected_crs(geom_ll)
        projected = reproject(geom_ll, WGS84, target)

        result: dict = {"status": MEASURED, "measurement_crs": crs_label(target)}
        if gtype in POLYGONAL:
            area, perimeter = projected.area, projected.length
            if not (math.isfinite(area) and math.isfinite(perimeter)):
                return {"status": ERROR, "note": "Projection produced non-finite coordinates."}
            result.update(
                area_sq_m=round(area, 4),
                area_hectares=round(area / 10_000, 6),
                perimeter_m=round(perimeter, 4),
            )
        else:
            length = projected.length
            if not math.isfinite(length):
                return {"status": ERROR, "note": "Projection produced non-finite coordinates."}
            result.update(length_m=round(length, 4))

        if not geom2d.is_valid:
            result["note"] = f"Invalid geometry ({explain_validity(geom2d)}); result may be inaccurate."
        return result
    except Exception as exc:  # noqa: BLE001 - one bad feature must not fail the whole file
        return {"status": ERROR, "note": f"Measurement failed: {exc}"}
