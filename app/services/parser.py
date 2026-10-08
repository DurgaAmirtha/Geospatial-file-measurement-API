"""Read Shapefile (.zip) and KML files into a flat list of features."""
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pyogrio
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app import config


class ProcessingError(Exception):
    """The file is unreadable or unusable. Message is safe to show to the client."""


@dataclass
class ParsedFeature:
    index: int
    layer: str | None
    geometry: BaseGeometry | None
    geojson: dict | None
    properties: dict
    crs: CRS


def _safe_extract(zip_path: Path, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ProcessingError("File is not a valid zip archive.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > config.MAX_ZIP_MEMBERS:
            raise ProcessingError("Zip archive contains too many files.")
        if sum(i.file_size for i in infos) > config.MAX_UNZIPPED_MB * 1024 * 1024:
            raise ProcessingError("Zip archive is too large when uncompressed.")
        root = dest.resolve()
        for info in infos:
            target = (dest / info.filename).resolve()
            if root != target and root not in target.parents:  # zip-slip guard
                raise ProcessingError("Zip archive contains unsafe paths.")
        zf.extractall(dest)


def _frame_to_features(gdf, layer: str | None, start: int, assume_crs: str | None) -> list[ParsedFeature]:
    if not isinstance(gdf, gpd.GeoDataFrame):  # attribute-only table, no geometry
        return []
    crs = gdf.crs
    if crs is None:
        if not assume_crs:
            raise ProcessingError(
                "The file has no CRS (e.g. missing .prj). Re-upload with ?assume_crs=EPSG:xxxx."
            )
        try:
            crs = CRS.from_user_input(assume_crs)
        except Exception as exc:  # noqa: BLE001
            raise ProcessingError(f"Invalid assume_crs value: {assume_crs!r}") from exc

    # to_json gives clean GeoJSON geometries and JSON-safe properties (NaN -> null, dates -> str)
    collection = json.loads(gdf.to_json(na="null", drop_id=True, default=str))
    out = []
    for offset, (geom, feat) in enumerate(zip(gdf.geometry, collection["features"])):
        out.append(
            ParsedFeature(
                index=start + offset,
                layer=layer,
                geometry=geom,
                geojson=feat.get("geometry"),
                properties=feat.get("properties") or {},
                crs=crs,
            )
        )
    return out


def _read_all_layers(path: Path, assume_crs: str | None, start: int) -> list[ParsedFeature]:
    features: list[ParsedFeature] = []
    try:
        for name, _ in pyogrio.list_layers(path):
            gdf = gpd.read_file(path, layer=name, engine="pyogrio")
            features.extend(_frame_to_features(gdf, str(name), start + len(features), assume_crs))
    except ProcessingError:
        raise
    except Exception as exc:  # noqa: BLE001 - GDAL raises many exception types
        raise ProcessingError(f"Could not read geospatial data from '{path.name}': {exc}") from exc
    return features


def parse_kml(path: Path, assume_crs: str | None = None) -> list[ParsedFeature]:
    # KML is defined to be WGS84, so a missing CRS can safely default to it.
    return _read_all_layers(path, assume_crs or "EPSG:4326", 0)


def parse_shapefile_zip(zip_path: Path, workdir: Path, assume_crs: str | None = None) -> list[ParsedFeature]:
    extract_dir = workdir / "unzipped"
    extract_dir.mkdir()
    _safe_extract(zip_path, extract_dir)
    shp_files = sorted(
        p for p in extract_dir.rglob("*") if p.suffix.lower() == ".shp" and "__MACOSX" not in p.parts
    )
    if not shp_files:
        raise ProcessingError("No .shp file found inside the zip archive.")
    features: list[ParsedFeature] = []
    for shp in shp_files:
        features.extend(_read_all_layers(shp, assume_crs, len(features)))
    return features
