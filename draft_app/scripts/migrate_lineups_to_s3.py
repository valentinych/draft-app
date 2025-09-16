# scripts/migrate_lineups_to_s3.py
import os, json
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ["AWS_S3_BUCKET"]
REGION = os.getenv("AWS_DEFAULT_REGION", "eu-central-1")
LINEUPS_DIR = Path(os.getenv("LOCAL_LINEUPS_DIR", "/app/lineups"))
PREFIXES = [p.strip() for p in os.getenv("MIGRATE_PREFIXES", "GW1").split(",") if p.strip()]
DRY_RUN = os.getenv("MIGRATE_DRY_RUN", "false").lower() in ("1","true","yes")

s3 = boto3.client("s3", region_name=REGION)

def exists_in_s3(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NotFound", "NoSuchKey"):
            return False
        raise

def upload_json(path: Path, key: str):
    with path.open("rb") as f:
        if not DRY_RUN:
            s3.put_object(Bucket=BUCKET, Key=key, Body=f.read(), ContentType="application/json")
    print(("[dry-run] " if DRY_RUN else "") + f"put s3://{BUCKET}/{key}")

def migrate_prefix(prefix: str):
    root = LINEUPS_DIR / prefix
    if not root.exists():
        print(f"[skip] no local dir: {root}")
        return (0,0,0)
    total = uploaded = skipped = 0
    for p in root.rglob("*.json"):
        total += 1
        # ожидаем структуру: lineups/GW1/<lineup_id>.json ИЛИ <subdirs>/<file>.json
        rel = p.relative_to(LINEUPS_DIR)
        key = f"lineups/{rel.as_posix()}"        # зеркалим структуру папок 1:1
        if exists_in_s3(key):
            skipped += 1
            continue
        upload_json(p, key)
        uploaded += 1
    print(f"[{prefix}] uploaded={uploaded} skipped={skipped} total={total}")
    return (uploaded, skipped, total)

def main():
    if not LINEUPS_DIR.exists():
        print(f"[info] no local lineups dir {LINEUPS_DIR}, nothing to migrate")
        return
    summary = []
    for pref in PREFIXES:
        summary.append(migrate_prefix(pref))
    up = sum(x[0] for x in summary); sk = sum(x[1] for x in summary); tot = sum(x[2] for x in summary)
    print(f"[done] uploaded={up} skipped={sk} total={tot} dry_run={DRY_RUN}")

if __name__ == "__main__":
    main()
