# Geospatial File Measurement API

A FastAPI service that accepts a **KML** file or a **zipped Shapefile**, extracts every feature
(ID, geometry type, geometry, CRS, properties) and returns **area** (polygons) and **length** (lines),
computed in a *projected, metric* CRS, never in lat/lon degrees.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

- Interactive docs (Swagger): http://127.0.0.1:8000/docs
- Run tests: `pytest -q`
- Docker: `docker build -t geo-api . && docker run -p 8000:8000 geo-api`

Configuration (environment variables, all optional):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./geo_measure.db` | Any SQLAlchemy URL (e.g. Postgres) |
| `MAX_UPLOAD_MB` | `50` | Max upload size |
| `MAX_UNZIPPED_MB` | `250` | Zip-bomb guard |
| `MAX_ZIP_MEMBERS` | `500` | Zip-bomb guard |

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/files/` | Upload and process a `.kml` or `.zip` (Shapefile) |
| GET | `/api/files/{id}/` | File info (status, CRS, feature count) |
| GET | `/api/files/{id}/features/` | Features with geometry (GeoJSON) and properties |
| GET | `/api/files/{id}/measurements/` | Per-feature measurements + file totals |
| DELETE | `/api/files/{id}/` | Delete a file and its features |

List endpoints accept `limit` (1-1000, default 100) and `offset`.
`/measurements/` also accepts `include_geometry=true`.

### Upload

```bash
curl -F "file=@survey.kml" http://127.0.0.1:8000/api/files/
curl -F "file=@parcels.zip" "http://127.0.0.1:8000/api/files/?assume_crs=EPSG:4326"
```

`assume_crs` is optional and only used when the file has no CRS (e.g. a Shapefile without `.prj`).

```json
{
  "id": "140cabd1e433444bbbd9ea0a81dd5baa",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": null,
  "created_at": "2026-10-08T04:47:52.486768"
}
```

Errors: `415` unsupported extension, `413` too large, `422` unreadable/invalid file
(body: `{"detail": "...", "id": "...", "status": "FAILED"}`; the record is kept so `GET /api/files/{id}/` shows `FAILED`),
`404` unknown id, `409` asking for features/measurements of a file that is not `COMPLETED`.

### Measurements

`GET /api/files/{id}/measurements/`

```json
{
  "file_id": "140cabd1e433444bbbd9ea0a81dd5baa",
  "total": 3, "limit": 100, "offset": 0,
  "totals": {
    "total_area_sq_m": 1200592.745, "total_length_m": 11065.1037,
    "measured": 2, "not_required": 1, "unsupported_or_error": 0
  },
  "results": [
    {
      "feature_id": 0, "layer": "upload", "geometry_type": "Polygon", "crs": "EPSG:4326",
      "measurement": {
        "status": "MEASURED", "measurement_crs": "EPSG:32643",
        "area_sq_m": 1200592.745, "area_hectares": 120.059274, "perimeter_m": 4383.0731
      }
    },
    {
      "feature_id": 1, "layer": "upload", "geometry_type": "LineString", "crs": "EPSG:4326",
      "measurement": { "status": "MEASURED", "measurement_crs": "EPSG:32643", "length_m": 11065.1037 }
    },
    {
      "feature_id": 2, "layer": "upload", "geometry_type": "Point", "crs": "EPSG:4326",
      "measurement": { "status": "NOT_REQUIRED", "note": "No measurement is defined for points." }
    }
  ]
}
```

(Null fields omitted above for brevity; the API returns them as `null`.)

Measurement `status` values:

| Status | Meaning |
|---|---|
| `MEASURED` | Polygon/MultiPolygon (area, perimeter) or LineString/MultiLineString (length) |
| `NOT_REQUIRED` | Point/MultiPoint |
| `UNSUPPORTED` | Empty/missing geometry or other types (e.g. GeometryCollection) |
| `ERROR` | Measurement failed for this feature (reason in `note`); other features are unaffected |

## Architecture

```
app/
  main.py              FastAPI app, startup (creates tables)
  config.py, db.py     Settings, SQLAlchemy engine/session
  models.py            UploadedFile, Feature tables
  schemas.py           Pydantic response models
  routers/files.py     HTTP layer only: validation, status codes
  services/
    parser.py          KML / zipped-Shapefile -> list[ParsedFeature]
    crs.py             CRS labelling, reprojection, projected-CRS selection
    measurements.py    measure_geometry(geom, crs) -> dict (never raises)
    processing.py      Orchestration: parse -> measure -> persist
tests/                 End-to-end API tests with accuracy checks
```

**File-processing flow**
1. `POST /api/files/` validates the extension and streams the upload to a temp dir (size-capped).
2. A `UploadedFile` row is created with status `PROCESSING`.
3. For a zip: safe extraction (zip-slip, member-count and uncompressed-size checks), then every `.shp` is read.
   For KML: every layer in the file is read. Reading uses GeoPandas with the pyogrio (GDAL) engine.
4. Each row becomes a feature: index, layer, geometry type, GeoJSON geometry (original CRS), properties, CRS.
5. Each feature is measured, then all features are saved in one transaction and the file is marked `COMPLETED`.
   Any failure marks the file `FAILED` with a client-safe message.

**Measurement flow** (`services/measurements.py`)
1. Points -> `NOT_REQUIRED`; empty/other types -> `UNSUPPORTED`; polygons/lines continue.
2. Drop Z (KML usually has it); lengths/areas are horizontal.
3. Reproject to WGS84 (if not already), choose a local metric CRS, reproject again.
4. Polygon: `area`, `perimeter`. Line: `length`. Values are rounded and stored with the CRS used.
5. Invalid geometries (e.g. self-intersecting rings) are still measured but carry a `note`.
   Any exception is caught per feature and reported as `ERROR`.

**CRS handling**
- KML is always WGS84. Shapefiles use the CRS from the `.prj`; if missing, the upload is rejected with a
  clear message unless `?assume_crs=` is supplied.
- Source CRS is *always* normalised to WGS84 first, then projected to a local CRS. This means
  already-projected inputs with high distortion (e.g. Web Mercator, EPSG:3857) are not trusted for
  measurement, and inputs in feet are handled implicitly (output is always metres).
- Local CRS selection, per feature: the **UTM zone** (EPSG:326xx north / 327xx south) of the geometry's
  representative point; polar stereographic (EPSG:3995 / 3031) beyond 84°N / 80°S.
- `always_xy=True` is used for all transforms to avoid the lat/lon vs lon/lat axis-order pitfall.
- Tests compare results against WGS84 ellipsoidal (geodesic) values from `pyproj.Geod`.

## Design Decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| **FastAPI** | Typed models, auto OpenAPI docs, small footprint | Django + DRF (heavier; GeoDjango would add a GDAL/PostGIS dependency chain for little gain here) |
| **GeoPandas + pyogrio** | One reader for Shapefile and KML, ships with GDAL wheels, fast | Fiona (slower, older), `fastkml` / `pyshp` (per-format parsers, more code) |
| **Per-feature UTM zone** | Accurate (<~0.1% distortion inside a zone), simple, no custom PROJ strings | A single CRS per file (breaks for files spanning zones); per-feature azimuthal equal-area (more accurate for large areas, but non-standard CRS strings are harder to audit); geodesic-only (`Geod`) is the most accurate but the requirement asks for a projection-based flow |
| **Measure at upload time and persist** | `GET` endpoints are cheap and deterministic; CRS used is recorded with the result | Compute lazily on each GET (repeats expensive work) |
| **Synchronous processing** | Simple, gives immediate result/errors; fine for the 50 MB cap | Background worker (Celery/RQ/`BackgroundTasks`) - see future scope |
| **SQLite + SQLAlchemy** | Zero setup; swap via `DATABASE_URL` | Postgres/PostGIS (better for spatial queries at scale) |
| **Features stored as GeoJSON/JSON columns** | Portable across DBs; keeps original CRS geometry intact | PostGIS geometry columns |
| **Per-feature failure isolation** | One weird geometry shouldn't fail a 10k-feature file | Fail whole file |
| **422 + persisted `FAILED` record for bad files** | Client gets an immediate error *and* an id to inspect | 400 with no record |

Security: filename is basename-only, upload size is capped while streaming, zips are checked for
path traversal and zip bombs, and uploaded files are processed in a temp dir that is always cleaned up.

## Learnings

- Lat/lon degrees are not a unit of length: 0.01° of longitude is ~1.11 km at the equator but ~0.8 km at 45°N.
  Verifying against `pyproj.Geod` was the easiest way to prove the pipeline is correct.
- Axis order is a real trap: EPSG:4326 is officially lat/lon, but GIS data is lon/lat. `always_xy=True` removes the ambiguity.
- Web Mercator (EPSG:3857) is a *display* CRS, not a *measurement* CRS. At 13°N its areas are inflated by ~5%,
  and the inflation grows rapidly towards the poles.
- GDAL's KML driver splits a file into layers, and Shapefiles cannot mix geometry types, so reading *all* layers matters.
- Real-world files are messy (no `.prj`, 3D coordinates, invalid rings, empty geometries); designing for per-feature
  failure from the start is easier than retrofitting it.

## Future Scope

- Async processing (Celery/RQ) with `PROCESSING` -> `COMPLETED` polling for very large files, plus upload progress.
- PostgreSQL + PostGIS, with spatial queries (bbox filter, intersects) on the features endpoint.
- Optional geodesic (`Geod`) measurements alongside projected ones, for cross-validation and very large polygons.
- Split geometries that cross UTM zone boundaries or the antimeridian.
- More formats: KMZ, GeoJSON, GeoPackage, and Shapefiles in a bare `.shp` + sidecar upload.
- Authentication, per-user file ownership, rate limiting, and file retention/cleanup policies.
- Alembic migrations, structured logging/metrics, CI (GitHub Actions) running tests and linting.
- Unit conversion options (`?units=acres|km2|ft`).
