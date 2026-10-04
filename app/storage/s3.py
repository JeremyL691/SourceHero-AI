"""S3-compatible object storage (AWS S3, Cloudflare R2, MinIO -- all speak this API)."""

from __future__ import annotations

import boto3
from botocore.config import Config

from app.core.config import get_settings


class S3Storage:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name=settings.s3_region,
        )
        self._bucket = settings.s3_bucket

    def upload(self, key: str, file, content_type: str = "application/octet-stream") -> str:
        self._client.upload_fileobj(file, self._bucket, key, ExtraArgs={"ContentType": content_type})
        return key

    def download(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


__all__ = ["S3Storage"]
