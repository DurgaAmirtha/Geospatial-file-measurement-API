"""Runtime configuration, overridable through environment variables."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./geo_measure.db")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_UNZIPPED_MB = int(os.getenv("MAX_UNZIPPED_MB", "250"))  # zip-bomb guard
MAX_ZIP_MEMBERS = int(os.getenv("MAX_ZIP_MEMBERS", "500"))
