#!/usr/bin/env python3
"""
Скрипт для проверки доступности составов в S3
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from draft_app.lineup_store import _slug_parts, S3_PREFIX, S3_BUCKET
from draft_app.config import EPL_USERS

def check_s3_access(bucket: str = None, prefix: str = "lineups"):
    """Проверяет доступ к S3 и показывает доступные составы"""
    bucket = bucket or S3_BUCKET or os.getenv("DRAFT_S3_BUCKET")
    
    if not bucket:
        print("❌ S3_BUCKET не указан")
        print("   Укажите через --bucket или переменную окружения DRAFT_S3_BUCKET")
        return
    
    print(f"📦 Проверяем доступ к S3: s3://{bucket}/{prefix}/")
    
    try:
        s3_client = boto3.client("s3")
        
        # Пробуем загрузить несколько составов для проверки
        managers = EPL_USERS
        found_count = 0
        
        for manager in managers:
            slug, _, _ = _slug_parts(manager)
            key = f"{prefix.rstrip('/')}/{slug}/gw1.json"
            
            try:
                obj = s3_client.get_object(Bucket=bucket, Key=key)
                body = obj.get("Body").read().decode("utf-8")
                import json
                data = json.loads(body)
                players = data.get("players", [])
                print(f"  ✅ {manager} (slug: {slug}): GW1 найден, {len(players)} игроков в старте")
                found_count += 1
            except ClientError as e:
                if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
                    print(f"  ⚠️  {manager} (slug: {slug}): GW1 не найден в S3")
                else:
                    print(f"  ❌ {manager}: ошибка доступа - {e}")
            except Exception as e:
                print(f"  ❌ {manager}: ошибка - {e}")
        
        print(f"\n📊 Найдено составов в S3: {found_count}/{len(managers)}")
        
        # Пробуем получить список всех составов
        print(f"\n📋 Получаем список всех составов из S3...")
        paginator = s3_client.get_paginator('list_objects_v2')
        total_lineups = 0
        by_manager = {}
        
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' not in page:
                continue
            for obj in page['Contents']:
                key = obj['Key']
                if key.endswith('.json') and '/gw' in key:
                    total_lineups += 1
                    parts = key.split('/')
                    if len(parts) >= 2:
                        user_slug = parts[-2]
                        by_manager.setdefault(user_slug, []).append(key)
        
        print(f"📊 Всего составов в S3: {total_lineups}")
        print(f"📊 По менеджерам:")
        for user_slug, keys in sorted(by_manager.items()):
            manager = None
            for m in managers:
                slug, _, _ = _slug_parts(m)
                if slug == user_slug:
                    manager = m
                    break
            manager_name = manager or user_slug
            print(f"  {manager_name}: {len(keys)} составов")
        
    except Exception as e:
        print(f"❌ Ошибка доступа к S3: {e}")
        print("   Убедитесь, что:")
        print("   1. AWS credentials настроены (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)")
        print("   2. Bucket существует и доступен")
        print("   3. Правильный регион указан")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Проверка доступности составов в S3")
    parser.add_argument("--bucket", help="S3 bucket name", default=None)
    parser.add_argument("--prefix", help="S3 prefix", default=S3_PREFIX or "lineups")
    
    args = parser.parse_args()
    check_s3_access(args.bucket, args.prefix)

