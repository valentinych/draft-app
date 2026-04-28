#!/usr/bin/env python3
"""
Ретроактивное исправление timestamp автосоставов для конкретного GW.

Баг: при заходе на страницу /epl/lineups до дедлайна тура для менеджеров
без сохранённого состава система автоматически копировала состав предыдущего
тура с ts = datetime.now(). Это давало автосоставу ранний ts (раньше дедлайна),
из-за чего в тай-брейке (сортировка по ts asc) такой автосостав обгонял
живые составы тех, кто реально выставлял состав позже (но до дедлайна).

Этот скрипт находит составы текущего GW, которые:
  1) полностью идентичны (players + bench) составу предыдущего GW, И
  2) имеют ts < deadline текущего GW
и переписывает их ts на дедлайн текущего GW (как и должен делать
исправленный код для будущих туров).

Использование:
    python scripts/fix_auto_lineup_ts.py --gw 34 --dry-run
    python scripts/fix_auto_lineup_ts.py --gw 34 --apply
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draft_app.config import EPL_USERS
from draft_app.lineup_store import load_lineup, save_lineup


def load_bootstrap() -> dict:
    p = Path(__file__).resolve().parent.parent / "players_fpl_bootstrap.json"
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def deadline_for_gw(bootstrap: dict, gw: int) -> datetime | None:
    for ev in (bootstrap.get("events") or []):
        if int(ev.get("id", 0)) == int(gw):
            dl = ev.get("deadline_time")
            if not dl:
                return None
            try:
                return datetime.fromisoformat(dl.replace("Z", "+00:00"))
            except Exception:
                return None
    return None


def lineup_signature(lineup: dict) -> tuple:
    players = tuple(sorted(int(x) for x in (lineup.get("players") or [])))
    bench = tuple(sorted(int(x) for x in (lineup.get("bench") or [])))
    return (players, bench)


def parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="Записать изменения (по умолчанию dry-run)")
    args = ap.parse_args()

    gw = args.gw
    bootstrap = load_bootstrap()
    deadline = deadline_for_gw(bootstrap, gw)
    if deadline is None:
        print(f"ERROR: не найден deadline для GW{gw}")
        return 1
    deadline_str = deadline.isoformat(timespec="seconds")
    print(f"GW{gw} deadline: {deadline_str}")

    fixed = 0
    skipped = 0
    for manager in EPL_USERS:
        cur = load_lineup(manager, gw, prefer_s3=True)
        if not cur:
            print(f"  [{manager}] нет состава GW{gw} — пропуск")
            continue
        prev = load_lineup(manager, gw - 1, prefer_s3=True)
        if not prev:
            skipped += 1
            print(f"  [{manager}] нет состава GW{gw-1} для сравнения — пропуск")
            continue
        cur_ts = parse_ts(cur.get("ts"))
        if cur_ts is None:
            skipped += 1
            print(f"  [{manager}] нет ts — пропуск")
            continue
        if lineup_signature(cur) != lineup_signature(prev):
            print(f"  [{manager}] состав изменён вручную (отличается от GW{gw-1}) — пропуск")
            continue
        if cur_ts >= deadline:
            print(f"  [{manager}] ts уже >= deadline ({cur.get('ts')}) — пропуск")
            continue
        print(f"  [{manager}] АВТОСОСТАВ: ts {cur.get('ts')} -> {deadline_str}")
        if args.apply:
            new_payload = dict(cur)
            new_payload["ts"] = deadline_str
            save_lineup(manager, gw, new_payload)
        fixed += 1

    print(f"\nИтого: {'обновлено' if args.apply else 'будет обновлено'} {fixed}, пропущено {skipped}")
    if not args.apply:
        print("Это dry-run. Запустите с --apply для записи.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
