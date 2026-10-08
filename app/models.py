import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "kml" | "shapefile"
    status: Mapped[str] = mapped_column(String(16), default="PROCESSING")
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.feature_index"
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), index=True)
    feature_index: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geometry_type: Mapped[str] = mapped_column(String(32))
    crs: Mapped[str] = mapped_column(String(255))
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON, original CRS
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    measurement: Mapped[dict] = mapped_column(JSON, default=dict)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
