# -*- coding: utf-8 -*-
"""Замер вклада каналов поиска на золотом наборе (аблация, как 2A/2B/2C в poisk2.py).
Запуск: python -m bot.search.zamer

Режимы:
  2A — только канал подгруппы Форы (без словаря)
  2B — только словарь + атрибуты (эталон стенда 1)
  2C — словарь + подгруппа + атрибуты (полный)
Прямые каналы (штрихкод/артикул) работают во всех режимах.
"""
import json
import os
import re
import sys

from .search import Poisk, load_products_json

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def zamer(p: Poisk, nabor, names_in_file, use_slovar, use_podgr, label):
    top1 = top3 = vsego = 0
    musor_ok = musor_vsego = 0
    net = 0
    provaly = []
    for k in nabor:
        pat = k["ждём"]
        res, kanal = p.iskat(k["запрос"], use_slovar=use_slovar, use_podgr=use_podgr)
        if pat is None:
            musor_vsego += 1
            if not res:
                musor_ok += 1
            else:
                provaly.append((k["запрос"], "выдумал: " + res[0]["imya"]))
            continue
        if not any(re.search(pat, n, re.I) for n in names_in_file):
            net += 1
            continue
        vsego += 1
        ok1 = bool(res) and re.search(pat, res[0]["imya"], re.I)
        ok3 = any(re.search(pat, r["imya"], re.I) for r in res[:3])
        top1 += bool(ok1)
        top3 += bool(ok3)
        if not ok1:
            provaly.append((k["запрос"], (res[0]["imya"] if res else "ПУСТО") + f"  [{kanal}]"))
    print(f"\n===== {label} =====")
    print(f"  top-1: {top1}/{vsego} = {top1 / vsego * 100:.0f}%   "
          f"top-3: {top3}/{vsego} = {top3 / vsego * 100:.0f}%   "
          f"мусор откл.: {musor_ok}/{musor_vsego}   нет в файле: {net}")
    if provaly:
        for qq, got in provaly:
            print(f"    провал: {qq:34s} -> {got[:52]}")
    return top1, top3, vsego


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    tovary = load_products_json()
    p = Poisk(tovary)
    nabor = json.load(open(os.path.join(_DATA, "zolotoy_nabor.json"), encoding="utf-8"))
    names = {r.get("imya", "") for r in tovary}
    print(f"база: {p.размер_базы} товаров, золотой набор: {len(nabor)} кейсов")

    zamer(p, nabor, names, use_slovar=False, use_podgr=True,  label="2A. только подгруппа Форы (+прямые каналы)")
    zamer(p, nabor, names, use_slovar=True,  use_podgr=False, label="2B. только словарь+атрибуты (эталон стенда 1)")
    zamer(p, nabor, names, use_slovar=True,  use_podgr=True,  label="2C. словарь + подгруппа + атрибуты (полный)")


if __name__ == "__main__":
    main()
