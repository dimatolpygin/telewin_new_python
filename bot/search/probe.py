# -*- coding: utf-8 -*-
"""Прогон золотого набора через ядро поиска. Метрики top-1/top-3 + провалы.
Запуск: python -m bot.search.probe [--podgr]

Учитываем, что products.json — НОВЫЙ прайс: если ожидаемого товара нет в файле
вообще, кейс не засчитываем как провал (net_v_obraztse), как в poisk2.py.
"""
import json
import os
import re
import sys

from .search import Poisk, load_products_json

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    use_podgr = "--podgr" in sys.argv

    tovary = load_products_json()
    p = Poisk(tovary)
    print(f"база: {p.размер_базы} товаров  (канал подгруппы: {'вкл' if use_podgr else 'выкл'})\n")

    nabor = json.load(open(os.path.join(_DATA, "zolotoy_nabor.json"), encoding="utf-8"))
    names_in_file = {r.get("imya", "") for r in tovary}

    top1 = top3 = vsego = 0
    musor_ok = musor_vsego = 0
    net_v_obraztse = 0
    provaly = []

    for k in nabor:
        pat = k["ждём"]
        res, kanal = p.iskat(k["запрос"], use_podgr=use_podgr)
        if pat is None:
            musor_vsego += 1
            if not res:
                musor_ok += 1
            else:
                provaly.append((k["запрос"], "выдумал: " + res[0]["imya"]))
            continue
        # есть ли ожидаемый ответ в этом файле вообще
        if not any(re.search(pat, n, re.I) for n in names_in_file):
            net_v_obraztse += 1
            continue
        vsego += 1
        ok1 = bool(res) and re.search(pat, res[0]["imya"], re.I)
        ok3 = any(re.search(pat, r["imya"], re.I) for r in res[:3])
        top1 += bool(ok1)
        top3 += bool(ok3)
        if not ok1:
            got = (res[0]["imya"] if res else "ПУСТО") + f"  [{kanal}]"
            provaly.append((k["запрос"], got))

    pct = lambda a, b: f"{a}/{b} = {a / b * 100:.0f}%" if b else f"{a}/{b}"
    print(f"товарных (есть в файле): {vsego}")
    print(f"  top-1: {pct(top1, vsego)}")
    print(f"  top-3: {pct(top3, vsego)}")
    print(f"мусор честно отклонён: {pct(musor_ok, musor_vsego)}")
    print(f"нет в новом прайсе (не мерялись): {net_v_obraztse}")
    if provaly:
        print("\nпровалы:")
        for q, got in provaly:
            print(f"  {q:38s} -> {got[:56]}")


if __name__ == "__main__":
    main()
