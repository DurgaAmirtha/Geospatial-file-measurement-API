from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str
    error_message: str | None = None
    created_at: datetime


class Measurement(BaseModel):
    status: str  # MEASURED | NOT_REQUIRED | UNSUPPORTED | ERROR
    measurement_crs: str | None = None
    area_sq_m: float | None = None
    area_hectares: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    note: str | None = None


class FeatureMeasurement(BaseModel):
    feature_id: int
    layer: str | None
    geometry_type: str
    crs: str
    measurement: Measurement
    geometry: dict[str, Any] | None = None  # only with ?include_geometry=true


class MeasurementTotals(BaseModel):
    total_area_sq_m: float
    total_length_m: float
    measured: int
    not_required: int
    unsupported_or_error: int


class MeasurementsResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    totals: MeasurementTotals
    results: list[FeatureMeasurement]


class FeatureOut(BaseModel):
    feature_id: int
    layer: str | None
    geometry_type: str
    crs: str
    geometry: dict[str, Any] | None
    properties: dict[str, Any]


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureOut]
