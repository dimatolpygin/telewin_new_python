# -*- coding: utf-8 -*-
"""Этап 6 — прогон набора субагентов через ядро поиска, метрики по группам.

Набор `data/test_nabor.json` сгенерирован субагентами Haiku по 11 группам прайса
(сленг, опечатки, родовые слова + мусор) и провалидирован против прайса: у каждого
товарного кейса regex-метка `ждём` совпадает хотя бы с одним реальным именем товара.

Метрика — top-1/top-3 по имени товара (как в `probe.py`), в проде поиск идёт с
`use_podgr=True` (см. `ai/agent.py`) — тут так же по умолчанию.

Запуск:  python -m bot.search.test_subagenty [--no-podgr] [--db] [--json ПУТЬ]
"""
import asyncio
import collections
import json
import os
import re
import sys

from .search import Poisk, load_products_json

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def _load_tovary(use_db: bool) -> list[dict]:
    if not use_db:
        return load_products_json()
    from ..config import load_config
    from ..db import create_pool, zagruzit_vse_tovary

    async def _fetch():
        cfg = load_config()
        pool = await create_pool(cfg)
        try:
            return await zagruzit_vse_tovary(pool, cfg.pg.schema)
        finally:
            await pool.close()

    return asyncio.run(_fetch())


def progon(nabor: list[dict], p: Poisk, use_podgr: bool) -> dict:
    """Прогон набора. Возвращает агрегаты по группам + список провалов."""
    by_grp = collections.defaultdict(lambda: {"vsego": 0, "top1": 0, "top3": 0})
    by_priem = collections.defaultdict(lambda: {"vsego": 0, "top1": 0})
    provaly = []
    musor_ok = musor_vsego = 0

    for k in nabor:
        q = k["запрос"]
        pat = k.get("ждём")
        res, kanal = p.iskat(q, use_podgr=use_podgr)
        if pat is None:
            musor_vsego += 1
            if not res:
                musor_ok += 1
            else:
                provaly.append({"запрос": q, "группа": "МУСОР", "тип": "выдумал",
                                "получил": res[0]["imya"], "канал": kanal})
            continue

        grp = k.get("группа", "?")
        priem = k.get("прием", "?")
        rx = re.compile(pat, re.I)
        ok1 = bool(res) and rx.search(res[0]["imya"])
        ok3 = any(rx.search(r["imya"]) for r in res[:3])

        g = by_grp[grp]
        g["vsego"] += 1
        g["top1"] += bool(ok1)
        g["top3"] += bool(ok3)
        pr = by_priem[priem]
        pr["vsego"] += 1
        pr["top1"] += bool(ok1)

        if not ok1:
            provaly.append({"запрос": q, "группа": grp, "подгруппа": k.get("подгруппа", ""),
                            "прием": priem, "ждём": pat,
                            "получил": (res[0]["imya"] if res else "ПУСТО"),
                            "в_top3": bool(ok3), "канал": kanal})

    return {"by_grp": dict(by_grp), "by_priem": dict(by_priem),
            "musor_ok": musor_ok, "musor_vsego": musor_vsego, "provaly": provaly}


def _pct(a, b):
    return f"{a}/{b} = {a / b * 100:.0f}%" if b else f"{a}/{b}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    use_podgr = "--no-podgr" not in sys.argv
    use_db = "--db" in sys.argv
    path = _DATA + os.sep + "test_nabor.json"
    if "--json" in sys.argv:
        path = sys.argv[sys.argv.index("--json") + 1]

    nabor = json.load(open(path, encoding="utf-8"))
    tovary = _load_tovary(use_db)
    p = Poisk(tovary)
    istochnik = "Postgres" if use_db else "products.json"
    print(f"база: {p.размер_базы} товаров ({istochnik}); канал подгруппы: "
          f"{'вкл' if use_podgr else 'выкл'}; набор: {len(nabor)} запросов\n")

    r = progon(nabor, p, use_podgr)

    tot_v = sum(g["vsego"] for g in r["by_grp"].values())
    tot_1 = sum(g["top1"] for g in r["by_grp"].values())
    tot_3 = sum(g["top3"] for g in r["by_grp"].values())
    print("=== ИТОГО по товарным запросам ===")
    print(f"  top-1: {_pct(tot_1, tot_v)}")
    print(f"  top-3: {_pct(tot_3, tot_v)}")
    print(f"  мусор честно отклонён: {_pct(r['musor_ok'], r['musor_vsego'])}\n")

    print("=== В разрезе 11 групп (top-1 / top-3) ===")
    for grp, g in sorted(r["by_grp"].items(), key=lambda kv: -kv[1]["vsego"]):
        print(f"  {grp:42s} top-1 {_pct(g['top1'], g['vsego']):>12s}   "
              f"top-3 {_pct(g['top3'], g['vsego']):>12s}")

    print("\n=== В разрезе приёмов формулировки (top-1) ===")
    for priem, pr in sorted(r["by_priem"].items(), key=lambda kv: -kv[1]["vsego"]):
        print(f"  {priem:14s} {_pct(pr['top1'], pr['vsego'])}")

    if r["provaly"]:
        print(f"\n=== ПРОВАЛЫ ({len(r['provaly'])}) ===")
        for x in r["provaly"]:
            if x["группа"] == "МУСОР":
                print(f"  [мусор]  {x['запрос']:34s} -> выдумал: {x['получил'][:44]}")
            else:
                t3 = "  (есть в top-3)" if x.get("в_top3") else ""
                print(f"  [{x['группа'][:16]:16s}] {x['запрос']:32s} -> {x['получил'][:40]}{t3}")


if __name__ == "__main__":
    main()
