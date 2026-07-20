# -*- coding: utf-8 -*-
"""Замер «лексика vs гибрид» (этап 10). Прогоняет золотой и слепой наборы через
чистую лексику (`Poisk.iskat`) и гибрид (`Gibrid.iskat`, лексика ⊕ вектор RRF),
считает top-1/top-3 и печатает сравнение — общее и по группам.

Требует посчитанные эмбеддинги в БД (`python -m bot.embed_index --all`).
Запуск: python -m bot.search.zamer_gibrid
"""
import asyncio
import json
import os
import re
import sys
from collections import defaultdict

import asyncpg

from ..config import load_config
from .search import Poisk
from .vector import VectorKanal
from .gibrid import Gibrid

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def _load(name: str) -> list[dict]:
    return json.load(open(os.path.join(_DATA, name), encoding="utf-8"))


async def _прогон(набор, имена, лекс_fn, гибр_fn, метка: str) -> dict:
    """Возвращает агрегаты по обоим системам и по группам."""
    итог = {
        "лекс": {"top1": 0, "top3": 0, "mus_ok": 0},
        "гибр": {"top1": 0, "top3": 0, "mus_ok": 0},
        "всего": 0, "мусор": 0, "нет": 0,
        "группы": defaultdict(lambda: {"n": 0, "лекс1": 0, "гибр1": 0}),
        "выигрыш": [], "проигрыш": [],
    }
    for k in набор:
        pat = k.get("ждём")
        q = k["запрос"]
        лекс, _ = лекс_fn(q)
        гибр, _kан = await гибр_fn(q)
        if pat is None:  # мусор: успех = ничего не вернули
            итог["мусор"] += 1
            if not лекс:
                итог["лекс"]["mus_ok"] += 1
            if not гибр:
                итог["гибр"]["mus_ok"] += 1
            continue
        if not any(re.search(pat, n, re.I) for n in имена):
            итог["нет"] += 1
            continue
        итог["всего"] += 1
        гр = k.get("группа", "—")
        g = итог["группы"][гр]
        g["n"] += 1

        л1 = bool(лекс) and bool(re.search(pat, лекс[0]["imya"], re.I))
        л3 = any(re.search(pat, r["imya"], re.I) for r in лекс[:3])
        г1 = bool(гибр) and bool(re.search(pat, гибр[0]["imya"], re.I))
        г3 = any(re.search(pat, r["imya"], re.I) for r in гибр[:3])
        итог["лекс"]["top1"] += л1; итог["лекс"]["top3"] += л3
        итог["гибр"]["top1"] += г1; итог["гибр"]["top3"] += г3
        g["лекс1"] += л1; g["гибр1"] += г1
        if г1 and not л1:
            итог["выигрыш"].append((q, (гибр[0]["imya"] if гибр else "ПУСТО")))
        if л1 and not г1:
            итог["проигрыш"].append((q, (гибр[0]["imya"] if гибр else "ПУСТО")))
    return итог


def _печать(итог: dict, метка: str, out: list) -> None:
    в = итог["всего"]
    л, г = итог["лекс"], итог["гибр"]
    out.append(f"\n===== {метка} =====")
    out.append(f"  позиций: {в}   мусор: {итог['мусор']}   нет в файле: {итог['нет']}")

    def стр(name, s):
        t1 = s["top1"]; t3 = s["top3"]
        p1 = f"{t1/в*100:.0f}%" if в else "—"
        p3 = f"{t3/в*100:.0f}%" if в else "—"
        return f"  {name:8s} top-1: {t1}/{в} = {p1}   top-3: {t3}/{в} = {p3}   мусор откл.: {s['mus_ok']}/{итог['мусор']}"
    out.append(стр("лексика", л))
    out.append(стр("гибрид", г))
    d1 = г["top1"] - л["top1"]; d3 = г["top3"] - л["top3"]
    out.append(f"  дельта   top-1: {d1:+d}   top-3: {d3:+d}")

    out.append("  — по группам (лекс1 → гибр1 из n) —")
    for гр, s in sorted(итог["группы"].items(), key=lambda kv: -kv[1]["n"]):
        mark = ""
        if s["гибр1"] > s["лекс1"]:
            mark = "  ↑"
        elif s["гибр1"] < s["лекс1"]:
            mark = "  ↓ РЕГРЕСС"
        out.append(f"     {s['лекс1']:2d} → {s['гибр1']:2d} / {s['n']:2d}   {гр}{mark}")

    if итог["выигрыш"]:
        out.append(f"  + вектор вытащил ({len(итог['выигрыш'])}):")
        for q, im in итог["выигрыш"][:20]:
            out.append(f"     «{q}» → {im}")
    if итог["проигрыш"]:
        out.append(f"  - вектор сломал ({len(итог['проигрыш'])}):")
        for q, im in итог["проигрыш"][:20]:
            out.append(f"     «{q}» → {im}")


async def main() -> None:
    cfg = load_config()
    pool = await asyncpg.create_pool(
        host=cfg.pg.host, port=cfg.pg.port, user=cfg.pg.user,
        password=cfg.pg.password, database=cfg.pg.database, min_size=1, max_size=4,
    )
    try:
        from ..db import zagruzit_vse_tovary
        tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
        poisk = Poisk(tovary)
        vk = VectorKanal(pool, cfg.pg.schema)
        if not await vk.доступен():
            print("Эмбеддинги не посчитаны — сначала `python -m bot.embed_index --all`.")
            return
        gibrid = Gibrid(poisk, vk)
        имена = [t["imya"] for t in tovary]

        def лекс_fn(q):
            return poisk.iskat(q, use_podgr=True)

        async def гибр_fn(q):
            return await gibrid.iskat(q, use_podgr=True)

        out: list[str] = []
        for файл, метка in (("zolotoy_nabor.json", "ЗОЛОТОЙ"), ("test_nabor.json", "СЛЕПОЙ")):
            набор = _load(файл)
            res = await _прогон(набор, имена, лекс_fn, гибр_fn, метка)
            _печать(res, метка, out)

        текст = "\n".join(out)
        sys.stdout.reconfigure(encoding="utf-8")
        print(текст)
        if "--save" in sys.argv:
            with open(os.path.join(os.path.dirname(_DATA), "docs", "ZAMER_GIBRID.md"), "w", encoding="utf-8") as f:
                f.write("# Замер: лексика vs гибрид (этап 10)\n\n```\n" + текст + "\n```\n")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
