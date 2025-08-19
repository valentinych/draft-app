# scripts/backup_lineups.py
import os, sys, tarfile, time, boto3
from pathlib import Path

BUCKET = os.environ["AWS_S3_BUCKET"]
REGION = os.getenv("AWS_DEFAULT_REGION", "eu-central-1")
LINEUPS_DIR = Path(os.getenv("LOCAL_LINEUPS_DIR", "/app/lineups"))

def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = f"/tmp/lineups-backup-{ts}.tar.gz"
    if not LINEUPS_DIR.exists():
        print(f"[backup] no local dir {LINEUPS_DIR}, nothing to backup"); return
    with tarfile.open(out, "w:gz") as tar:
        tar.add(str(LINEUPS_DIR), arcname="lineups")
    s3 = boto3.client("s3", region_name=REGION)
    key = f"backups/{ts}/lineups-backup.tar.gz"
    s3.upload_file(out, BUCKET, key)
    print(f"[backup] uploaded to s3://{BUCKET}/{key}")

if __name__ == "__main__":
    main()
