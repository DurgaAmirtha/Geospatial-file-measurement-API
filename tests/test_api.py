import io
import zipfile

import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import LineString, Polygon

GEOD = Geod(ellps="WGS84")

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Parcel A</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.0,13.0,0 77.01,13.0,0 77.01,13.01,0 77.0,13.01,0 77.0,13.0,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Road</name><LineString><coordinates>77.0,13.0,0 77.0,13.1,0</coordinates></LineString></Placemark>
<Placemark><name>Pin</name><Point><coordinates>77.0,13.0,0</coordinates></Point></Placemark>
</Document></kml>"""

POLY = Polygon([(77.0, 13.0), (77.01, 13.0), (77.01, 13.01), (77.0, 13.01)])
LINE = LineString([(77.0, 13.0), (77.0, 13.1)])


def _upload(client, name, data, **params):
    return client.post("/api/files/", files={"file": (name, data)}, params=params)


def _zip_shapefile(tmp_path, gdf, with_prj=True):
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "layer.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in shp_dir.iterdir():
            if p.suffix == ".prj" and not with_prj:
                continue
            zf.write(p, p.name)
    return buf.getvalue()


def _by_type(results):
    return {r["geometry_type"]: r for r in results}


def test_kml_end_to_end(client):
    r = _upload(client, "survey.kml", KML.encode())
    assert r.status_code == 201
    info = r.json()
    assert info["status"] == "COMPLETED" and info["feature_count"] == 3 and info["crs"] == "EPSG:4326"

    assert client.get(f"/api/files/{info['id']}/").json()["filename"] == "survey.kml"

    m = client.get(f"/api/files/{info['id']}/measurements/").json()
    by_type = _by_type(m["results"])

    expected_area = abs(GEOD.geometry_area_perimeter(POLY)[0])
    area = by_type["Polygon"]["measurement"]["area_sq_m"]
    assert area == pytest.approx(expected_area, rel=0.005)  # NOT degrees^2
    assert by_type["Polygon"]["measurement"]["measurement_crs"] == "EPSG:32643"

    expected_len = GEOD.geometry_length(LINE)
    assert by_type["LineString"]["measurement"]["length_m"] == pytest.approx(expected_len, rel=0.001)

    assert by_type["Point"]["measurement"]["status"] == "NOT_REQUIRED"
    assert m["totals"]["measured"] == 2


def test_features_endpoint_returns_geometry_and_properties(client):
    fid = _upload(client, "s.kml", KML.encode()).json()["id"]
    feats = client.get(f"/api/files/{fid}/features/").json()["results"]
    assert {f["properties"]["Name"] for f in feats} == {"Parcel A", "Road", "Pin"}
    assert all(f["geometry"] and f["crs"] == "EPSG:4326" for f in feats)


def test_shapefile_wgs84(client, tmp_path):
    gdf = gpd.GeoDataFrame({"name": ["a"]}, geometry=[POLY], crs="EPSG:4326")
    fid = _upload(client, "x.zip", _zip_shapefile(tmp_path, gdf)).json()["id"]
    by_type = _by_type(client.get(f"/api/files/{fid}/measurements/").json()["results"])
    assert by_type["Polygon"]["measurement"]["area_sq_m"] == pytest.approx(
        abs(GEOD.geometry_area_perimeter(POLY)[0]), rel=0.005
    )


def test_projected_input_crs_is_handled(client, tmp_path):
    # Web Mercator inflates areas heavily at 13N; we must still return a true ground area.
    gdf = gpd.GeoDataFrame({"n": [1]}, geometry=[POLY], crs="EPSG:4326").to_crs(3857)
    fid = _upload(client, "merc.zip", _zip_shapefile(tmp_path, gdf)).json()["id"]
    r = client.get(f"/api/files/{fid}/measurements/").json()["results"][0]
    assert r["crs"] == "EPSG:3857"
    assert r["measurement"]["area_sq_m"] == pytest.approx(abs(GEOD.geometry_area_perimeter(POLY)[0]), rel=0.005)


def test_missing_prj_fails_then_assume_crs_works(client, tmp_path):
    gdf = gpd.GeoDataFrame({"n": [1]}, geometry=[POLY], crs="EPSG:4326")
    data = _zip_shapefile(tmp_path, gdf, with_prj=False)
    bad = _upload(client, "noprj.zip", data)
    assert bad.status_code == 422 and "assume_crs" in bad.json()["detail"]
    assert client.get(f"/api/files/{bad.json()['id']}/").json()["status"] == "FAILED"

    ok = _upload(client, "noprj.zip", data, assume_crs="EPSG:4326")
    assert ok.status_code == 201 and ok.json()["status"] == "COMPLETED"


def test_unsupported_extension(client):
    assert _upload(client, "a.txt", b"hi").status_code == 415


def test_garbage_kml_and_zip(client):
    assert _upload(client, "bad.kml", b"not xml").status_code == 422
    assert _upload(client, "bad.zip", b"not a zip").status_code == 422


def test_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    r = _upload(client, "empty.zip", buf.getvalue())
    assert r.status_code == 422 and ".shp" in r.json()["detail"]


def test_unknown_id_404(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_pagination(client):
    fid = _upload(client, "s.kml", KML.encode()).json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and len(page["results"]) == 1 and page["totals"]["measured"] == 2
