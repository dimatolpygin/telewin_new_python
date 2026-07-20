# -*- coding: utf-8 -*-
"""Выгрузка среза прайса для анализа: по группе(ам) — подгруппы, производители и
выборка реальных имён товаров. Инструмент прочёсывания прайса (аналитика апгрейда).

Запуск:  python -m bot.search.dump_group "<подстрока группы>" [--limit N]
Пример:  python -m bot.search.dump_group "Инструмент ручной" --limit 1200
Печатает в stdout (UTF-8): состав подгрупп, топ производителей, список имён.
"""
import asyncio
import sys

from ..config import load_config
from ..db import create_pool


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = 1200
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if not args:
        print("укажи подстроку группы", file=sys.stderr)
        sys.exit(1)
    grp = args[0]

    cfg = load_config()
    pool = await create_pool(cfg)
    s = cfg.pg.schema
    where = "gruppa ilike '%' || $1 || '%'"

    total = await pool.fetchval(f"select count(*) from {s}.products where {where}", grp)
    print(f"ГРУППА ~ «{grp}»: {total} позиций\n")

    print("=== ПОДГРУППЫ ===")
    for r in await pool.fetch(
        f"select podgruppa, count(*) c from {s}.products where {where} group by podgruppa order by c desc", grp):
        print(f"  {r['c']:4d}  {r['podgruppa']}")

    print("\n=== ПРОИЗВОДИТЕЛИ (top-25; учти: часто это СТРАНА, а не бренд) ===")
    for r in await pool.fetch(
        f"select proizvoditel, count(*) c from {s}.products where {where} and proizvoditel<>'' "
        f"group by proizvoditel order by c desc limit 25", grp):
        print(f"  {r['c']:4d}  {r['proizvoditel']}")

    print(f"\n=== ИМЕНА ТОВАРОВ (выборка до {limit}, сгруппированы по подгруппе) ===")
    rows = await pool.fetch(
        f"select podgruppa, imya, cena from {s}.products where {where} "
        f"order by podgruppa, imya limit {limit}", grp)
    cur = None
    for r in rows:
        if r["podgruppa"] != cur:
            cur = r["podgruppa"]
            print(f"\n-- [{cur}] --")
        print(f"  {r['imya']}")
    if total > limit:
        print(f"\n(показано {limit} из {total})")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
