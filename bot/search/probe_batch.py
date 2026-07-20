# -*- coding: utf-8 -*-
"""Батч-прогон произвольных запросов через ГИБРИД (лексика ⊕ вектор RRF).
Инструмент для UAT естественным языком: принимает файл с запросами (по одному
на строку, # — комментарий), печатает JSON-массив результатов на stdout.

Запуск:  python -m bot.search.probe_batch <файл_запросов> [--top N]
Каждый элемент: {"запрос", "канал", "результаты":[{ранг,имя,цена,остаток,производитель}]}.
"""
import asyncio
import json
import os
import sys

import asyncpg

from ..config import load_config
from ..db import zagruzit_vse_tovary
from .search import Poisk
from .vector import VectorKanal
from .gibrid import Gibrid


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = 5
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    if not args:
        print("укажи файл с запросами", file=sys.stderr)
        sys.exit(1)

    with open(args[0], encoding="utf-8") as f:
        queries = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    cfg = load_config()
    pool = await asyncpg.create_pool(
        host=cfg.pg.host, port=cfg.pg.port, user=cfg.pg.user,
        password=cfg.pg.password, database=cfg.pg.database, min_size=1, max_size=4,
    )
    try:
        tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
        poisk = Poisk(tovary)
        vk = VectorKanal(pool, cfg.pg.schema)
        gibrid = Gibrid(poisk, vk)

        out = []
        for q in queries:
            res, kanal = await gibrid.iskat(q, top=top, use_podgr=True)
            out.append({
                "запрос": q,
                "канал": kanal,
                "результаты": [
                    {
                        "ранг": i + 1,
                        "имя": t.get("imya", ""),
                        "цена": t.get("cena"),
                        "остаток": t.get("ostatok_obshiy"),
                        "производитель": t.get("proizvoditel", ""),
                    }
                    for i, t in enumerate(res)
                ],
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
