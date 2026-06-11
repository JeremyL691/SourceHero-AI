from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import settings


class R2Storage:
    def __init__(self) -> None:
        endpoint_url = settings.r2_endpoint or f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
        self._bucket = settings.r2_bucket_name
        self._public_url = settings.r2_public_url

    def upload(self, key: str, file, content_type: str = "application/octet-stream") -> str:
        self._client.upload_fileobj(file, self._bucket, key, ExtraArgs={"ContentType": content_type})
        if self._public_url:
            return f"{self._public_url.rstrip('/')}/{key}"
        return key

    def download(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
