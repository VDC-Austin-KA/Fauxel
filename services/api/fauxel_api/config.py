"""Environment-driven settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _repo_dir(*parts: str) -> str:
    # services/api/fauxel_api/config.py -> repo root is parents[3]
    return str(Path(__file__).resolve().parents[3].joinpath(*parts))


@dataclass
class Settings:
    database_url: str = "postgresql://fauxel:fauxel@localhost:5432/fauxel"
    storage: str = "s3"  # s3 | fs
    fs_root: str = "./.fauxel-data"
    s3_endpoint: str | None = None
    s3_bucket: str = "fauxel-media"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "auto"
    s3_public_endpoint: str | None = None  # endpoint browsers/phones use in presigned URLs (R2 S3 endpoint, http://localhost:9000 for MinIO)
    s3_addressing: str = "path"  # path | virtual
    max_direct_upload_mb: int = 20480
    auth: str = "on"  # on | off
    trust_access_header: bool = False
    key_salt: str = "change-me"
    admin_token: str | None = None
    kafka_bootstrap: str | None = None
    public_url: str = ""
    blur_min: float = 20.0
    max_frames_per_upload: int = 1500
    max_upload_mb: int = 2048
    auto_migrate: bool = True
    migrations_dir: str = field(default_factory=lambda: _repo_dir("db", "migrations"))
    contracts_dir: str = field(default_factory=lambda: _repo_dir("contracts"))

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        e = os.environ.get
        s = cls(
            database_url=e("FAUXEL_DATABASE_URL", cls.database_url),
            storage=e("FAUXEL_STORAGE", cls.storage),
            fs_root=e("FAUXEL_FS_ROOT", cls.fs_root),
            s3_endpoint=e("FAUXEL_S3_ENDPOINT"),
            s3_bucket=e("FAUXEL_S3_BUCKET", cls.s3_bucket),
            s3_access_key=e("FAUXEL_S3_ACCESS_KEY"),
            s3_secret_key=e("FAUXEL_S3_SECRET_KEY"),
            s3_region=e("FAUXEL_S3_REGION", cls.s3_region),
            s3_public_endpoint=e("FAUXEL_S3_PUBLIC_ENDPOINT"),
            s3_addressing=e("FAUXEL_S3_ADDRESSING", cls.s3_addressing),
            max_direct_upload_mb=int(e("FAUXEL_MAX_DIRECT_UPLOAD_MB", cls.max_direct_upload_mb)),
            auth=e("FAUXEL_AUTH", cls.auth),
            trust_access_header=e("FAUXEL_TRUST_ACCESS_HEADER", "0") == "1",
            key_salt=e("FAUXEL_KEY_SALT", cls.key_salt),
            admin_token=e("FAUXEL_ADMIN_TOKEN"),
            kafka_bootstrap=e("FAUXEL_KAFKA_BOOTSTRAP"),
            public_url=e("FAUXEL_PUBLIC_URL", "").rstrip("/"),
            blur_min=float(e("FAUXEL_BLUR_MIN", cls.blur_min)),
            max_frames_per_upload=int(e("FAUXEL_MAX_FRAMES", cls.max_frames_per_upload)),
            max_upload_mb=int(e("FAUXEL_MAX_UPLOAD_MB", cls.max_upload_mb)),
            auto_migrate=e("FAUXEL_AUTO_MIGRATE", "1") == "1",
        )
        if e("FAUXEL_MIGRATIONS_DIR"):
            s.migrations_dir = e("FAUXEL_MIGRATIONS_DIR")
        if e("FAUXEL_CONTRACTS_DIR"):
            s.contracts_dir = e("FAUXEL_CONTRACTS_DIR")
        for k, v in overrides.items():
            setattr(s, k, v)
        return s
