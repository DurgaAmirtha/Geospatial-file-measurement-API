"""CRS helpers: labelling, reprojection and choice of a local projected CRS."""
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else (crs.name or "UNKNOWN")


@lru_cache(maxsize=256)
def _transformer(src: CRS, dst: CRS) -> Transformer:
    # always_xy => (lon, lat) / (x, y) regardless of the CRS's official axis order
    return Transformer.from_crs(src, dst, always_xy=True)


def reproject(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    transformer = _transformer(src, dst)

    def _apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, _apply)


def pick_projected_crs(geom_lonlat: BaseGeometry) -> CRS:
    """Choose a metric CRS suited to a geometry given in WGS84 lon/lat.

    * Polar regions   -> polar stereographic (EPSG:3995 Arctic / EPSG:3031 Antarctic)
    * Everywhere else -> the UTM zone containing the geometry's representative point
    """
    pt = geom_lonlat.representative_point()
    lon, lat = pt.x, pt.y
    if lat >= 84:
        return CRS.from_epsg(3995)
    if lat <= -80:
        return CRS.from_epsg(3031)
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)
