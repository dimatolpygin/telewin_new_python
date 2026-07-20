# -*- coding: utf-8 -*-
"""Экспорт нового ПОЛНОГО 11-колоночного прайса (2026-07-18) в products.json
   для импорта в Postgres. Категории (группа/подгруппа) и штрихкод (EAN) теперь
   есть у всех позиций прямо в файле — отдельный справочник не нужен.
   Python читает .xls надёжнее SheetJS (старый формат, кодировка cp1251).

   Раскладка колонок нового файла (0-based):
     0 артикул   1 штрихкод(EAN)   2 наименование   3 единица   4 производитель
     5 цена   6 остаток общий   7 остаток Микро   8 остаток Берёзовская
     9 группа   10 подгруппа
"""
import xlrd, json, os

PRICE = os.environ.get("PRICE_XLS") or \
    r"C:\Users\GigaChat\Downloads\ЗАКАЗЫ\telewin\samples\2026-07-18T11-29-30.xls"
OUT = os.path.join(os.path.dirname(__file__), "products.json")


def na(v):
    return str(v).strip().strip("\t").strip()


def family_of(n):
    p = n.split()
    if not p:
        return "?"
    w = p[0].strip(",.")
    if w.upper() == "ШС" and len(p) > 1:
        return "ШС " + p[1].strip(",.")
    return w


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


b = xlrd.open_workbook(PRICE, encoding_override="cp1251")
sh = b.sheet_by_index(0)
out = []
for r in range(sh.nrows):
    v = [na(sh.cell_value(r, c)) for c in range(11)]
    out.append({
        "artikul": v[0],
        "shtrihkod": v[1],
        "imya": v[2],
        "edinica": v[3],
        "proizvoditel": v[4],
        "cena": num(v[5]),
        "ostatok_obshiy": num(v[6]),
        "ostatok_mikro": num(v[7]),
        "ostatok_berez": num(v[8]),
        "semeystvo": family_of(v[2]),
        "gruppa": v[9],
        "podgruppa": v[10],
    })

json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
scat = sum(1 for o in out if o["podgruppa"])
sean = sum(1 for o in out if o["shtrihkod"] and o["shtrihkod"] != "0")
print(f"экспортировано {len(out)} товаров -> {OUT}")
print(f"  с категорией: {scat} ({scat/len(out)*100:.0f}%)")
print(f"  со штрихкодом: {sean} ({sean/len(out)*100:.0f}%)")
