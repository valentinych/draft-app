"""Utility to upload a test file ``ucl/1.json`` to the configured S3 bucket."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_KEY = "ucl/1.json"
DEFAULT_PAYLOAD: Dict[str, Any] = {"message": "ucl test", "ok": True}


def _bucket() -> str:
    for env in ("DRAFT_S3_BUCKET", "AWS_S3_BUCKET"):
        raw = (os.getenv(env) or "").strip()
        if raw:
            return raw
    return ""


def _client():
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("s3", region_name=region)


def main() -> int:
    bucket = _bucket()
    if not bucket:
        print("[error] Neither DRAFT_S3_BUCKET nor AWS_S3_BUCKET is set.", file=sys.stderr)
        return 1
    key = os.getenv("TEST_UCL_KEY", DEFAULT_KEY).strip() or DEFAULT_KEY
    payload = DEFAULT_PAYLOAD
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    client = _client()
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-cache",
        )
    except (ClientError, BotoCoreError) as exc:
        print(f"[error] Failed to upload to s3://{bucket}/{key}: {exc}", file=sys.stderr)
        return 1
    print(f"[ok] Uploaded test payload to s3://{bucket}/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
