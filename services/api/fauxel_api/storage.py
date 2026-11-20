"""Object storage: S3 API (MinIO / Cloudflare R2) or a local directory for tests / fully local use.

Both backends implement the same multipart interface:
  create_multipart / presign_part / list_parts / put_part / complete_multipart / abort_multipart / size
`presign_part` returns None on the fs backend: the API then serves `PUT /uploads/{id}/parts/{n}` itself.
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from .config import Settings


class FsStorage:
    presigned = False

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("bad storage key")
        return p

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._p(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key)

    def download(self, key: str, dest: str) -> None:
        try:
            shutil.copyfile(self._p(key), dest)
        except FileNotFoundError:
            raise KeyError(key)

    def size(self, key: str) -> int | None:
        p = self._p(key)
        return p.stat().st_size if p.is_file() else None

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def ping(self) -> bool:
        return self.root.is_dir()

    # ---- multipart ----
    def _mp_dir(self, upload_id: str) -> Path:
        if not upload_id.isalnum():
            raise ValueError("bad upload id")
        return self.root / ".multipart" / upload_id

    def create_multipart(self, key: str, content_type: str | None = None) -> str:
        uid = uuid.uuid4().hex
        self._mp_dir(uid).mkdir(parents=True, exist_ok=True)
        return uid

    def presign_part(self, key: str, upload_id: str, n: int, expires: int = 3600) -> str | None:
        return None

    def put_part(self, key: str, upload_id: str, n: int, data: bytes) -> str:
        d = self._mp_dir(upload_id)
        if not d.is_dir():
            raise KeyError(upload_id)
        (d / f"{n:05d}").write_bytes(data)
        return hashlib.md5(data).hexdigest()

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        d = self._mp_dir(upload_id)
        if not d.is_dir():
            return []
        return [{"part_number": int(f.name), "size": f.stat().st_size, "etag": hashlib.md5(f.read_bytes()).hexdigest()}
                for f in sorted(d.iterdir()) if f.name.isdigit()]

    def complete_multipart(self, key: str, upload_id: str, parts: list[dict]) -> None:
        have = {p["part_number"]: p for p in self.list_parts(key, upload_id)}
        for p in parts:
            h = have.get(p["part_number"])
            if not h or h["etag"].strip('"') != str(p["etag"]).strip('"'):
                raise ValueError(f"part {p['part_number']} missing or etag mismatch")
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as out:
            for p in sorted(parts, key=lambda p: p["part_number"]):
                with open(self._mp_dir(upload_id) / f"{p['part_number']:05d}", "rb") as f:
                    shutil.copyfileobj(f, out, 1024 * 1024)
        tmp.replace(dest)
        shutil.rmtree(self._mp_dir(upload_id), ignore_errors=True)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        shutil.rmtree(self._mp_dir(upload_id), ignore_errors=True)


class S3Storage:
    presigned = True

    def __init__(self, s: Settings):
        import boto3
        from botocore.config import Config

        self.bucket = s.s3_bucket
        cfg = Config(signature_version="s3v4", s3={"addressing_style": s.s3_addressing if s.s3_addressing in ("path", "virtual") else "path"},
                     retries={"max_attempts": 3, "mode": "standard"})
        kw = dict(aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key, region_name=s.s3_region, config=cfg)
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint, **kw)
        # presigning is offline: a separate client signs against the endpoint that browsers / phones can reach
        self.public = boto3.client("s3", endpoint_url=s.s3_public_endpoint or s.s3_endpoint, **kw)

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            raise KeyError(key)

    def download(self, key: str, dest: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.download_file(self.bucket, key, dest)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                raise KeyError(key)
            raise

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def ping(self) -> bool:
        self.client.head_bucket(Bucket=self.bucket)
        return True

    # ---- multipart ----
    def create_multipart(self, key: str, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        return self.client.create_multipart_upload(Bucket=self.bucket, Key=key, **extra)["UploadId"]

    def presign_part(self, key: str, upload_id: str, n: int, expires: int = 3600) -> str | None:
        return self.public.generate_presigned_url(
            "upload_part", Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": n}, ExpiresIn=expires)

    def put_part(self, key: str, upload_id: str, n: int, data: bytes) -> str:
        r = self.client.upload_part(Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=n, Body=data)
        return r["ETag"].strip('"')

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        out, marker = [], 0
        while True:
            r = self.client.list_parts(Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumberMarker=marker, MaxParts=1000)
            out += [{"part_number": p["PartNumber"], "size": p["Size"], "etag": p["ETag"].strip('"')} for p in r.get("Parts", [])]
            if not r.get("IsTruncated"):
                return out
            marker = r["NextPartNumberMarker"]

    def complete_multipart(self, key: str, upload_id: str, parts: list[dict]) -> None:
        self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": p["part_number"], "ETag": f'"{str(p["etag"]).strip(chr(34))}"'}
                                       for p in sorted(parts, key=lambda p: p["part_number"])]})

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)


def make_storage(s: Settings):
    return FsStorage(s.fs_root) if s.storage == "fs" else S3Storage(s)
