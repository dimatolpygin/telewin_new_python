# -*- coding: utf-8 -*-
"""Генератор ТЕСТ-фикстура «новый прайс прилетел на FTP» для Milestone 3.

Берёт боевой ПОЛНЫЙ 11-колоночный прайс (2026-07-18, 11 864 строки), НЕ меняя
раскладку колонок, и создаёт его модификацию:
  1) меняет ЦЕНЫ у детерминированного подмножества позиций (каждая 5-я строка);
  2) дописывает 3 фейковые НОВЫЕ позиции:
       - 1 из ИЗВЕСТНОГО семейства (Лампа) — должна искаться сразу;
       - 2 из НОВЫХ семейств (Пирометр, Гигрометр) — сигнал монитору новизны (этап 21).
Рядом пишет manifest.json — что именно изменено (для приёмочных проверок этапов 19–22:
точные old→new цены, артикулы новинок, ожидаемая новизна семейств).

Формат выхода — старый .xls (BIFF8, как отдаёт Фора), запись через xlwt.

Запуск: python -m data.make_test_price
"""
import json
import os

import xlrd
import xlwt

SRC = os.environ.get("PRICE_XLS") or \
    r"C:\Users\GigaChat\Downloads\ЗАКАЗЫ\telewin\samples\2026-07-18T11-29-30.xls"
OUT_XLS = os.environ.get("OUT_XLS") or \
    r"C:\Users\GigaChat\Downloads\ЗАКАЗЫ\telewin\samples\ТЕСТ_2026-07-19T09-00-00.xls"
OUT_MANIFEST = os.path.join(os.path.dirname(__file__), "test_price_manifest.json")

# Раскладка нового файла (0-based): 0 артикул 1 штрихкод 2 имя 3 ед 4 произв
#   5 ЦЕНА 6 ост.общий 7 ост.Микро 8 ост.Берёз 9 группа 10 подгруппа
COL_CENA = 5

# Фейковые новинки: (артикул, штрихкод, имя, ед, произв, цена, ост_общ, ост_мик, ост_бер, группа, подгруппа)
NOVINKI = [
    # известное семейство «Лампа» + существующая подгруппа «Лампы» — обязана искаться сразу
    # (жёсткие каналы + вектор) и НЕ сигналить монитору новизны ни как семейство, ни как подгруппа
    ("TEST-0001", "2000000000017", "Лампа светодиодная ТЕСТ E27 9Вт 4000К груша",
     "шт", "ЭРА", 129.0, 40, 25, 15, "Электротовары", "Лампы"),
    # новое семейство «Пирометр» — должен поймать монитор новизны (этап 21)
    ("TEST-0002", "2000000000024", "Пирометр инфракрасный ТЕСТ -50..380C DT-380",
     "шт", "RGK", 1890.0, 6, 4, 2, "Инструмент", "Измерительный инструмент"),
    # новое семейство «Гигрометр» — второй сигнал монитору
    ("TEST-0003", "2000000000031", "Гигрометр цифровой ТЕСТ комнатный HTC-1",
     "шт", "RGK", 490.0, 12, 8, 4, "Инструмент", "Измерительный инструмент"),
]


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return None


def main():
    b = xlrd.open_workbook(SRC, encoding_override="cp1251")
    sh = b.sheet_by_index(0)
    ncols = sh.ncols
    print(f"источник: {SRC}\n  строк {sh.nrows}, колонок {ncols}")

    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("price")

    changed = []  # [(артикул, имя, old, new), ...] — только первые для manifest
    n_changed = 0
    for r in range(sh.nrows):
        row = [sh.cell_value(r, c) for c in range(ncols)]
        # каждая 5-я строка (детерминированно) — новая цена +5%, округление до копейки
        if r % 5 == 0:
            old = num(row[COL_CENA])
            if old is not None and old > 0:
                new = round(old * 1.05, 2)
                row[COL_CENA] = new
                n_changed += 1
                if len(changed) < 20:
                    art = str(row[0]).strip()
                    imya = str(row[2]).strip()
                    changed.append({"artikul": art, "imya": imya, "old": old, "new": new})
        for c in range(ncols):
            ws.write(r, c, row[c])

    # дописать новинки
    base = sh.nrows
    for i, nov in enumerate(NOVINKI):
        for c in range(min(ncols, len(nov))):
            ws.write(base + i, c, nov[c])

    wb.save(OUT_XLS)
    total = sh.nrows + len(NOVINKI)
    print(f"записано: {OUT_XLS}\n  строк {total} (было {sh.nrows} + {len(NOVINKI)} новинок)")
    print(f"  цен изменено: {n_changed}")

    manifest = {
        "source_file": os.path.basename(SRC),
        "output_file": os.path.basename(OUT_XLS),
        "rows_total": total,
        "prices_changed": n_changed,
        "price_rule": "каждая 5-я строка (r%5==0), цена *1.05 округл. до копейки",
        "changed_sample": changed,
        "new_positions": [
            {"artikul": n[0], "shtrihkod": n[1], "imya": n[2], "cena": n[5],
             "semeystvo": n[2].split()[0], "ожидание": exp}
            for n, exp in zip(
                NOVINKI,
                ["известное семейство Лампа — ищется сразу, монитор молчит",
                 "новое семейство Пирометр — монитор новизны сигналит",
                 "новое семейство Гигрометр — монитор новизны сигналит"],
            )
        ],
    }
    json.dump(manifest, open(OUT_MANIFEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"манифест: {OUT_MANIFEST}")


if __name__ == "__main__":
    main()
