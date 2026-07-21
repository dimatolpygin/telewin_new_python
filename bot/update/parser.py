# -*- coding: utf-8 -*-
"""Этап 18 — парсер прайса с автоопределением раскладки (8-кол старый ↔ 11-кол новый).

Раскладки Форы (0-based):
  11-кол (боевой с 18.07): 0 артикул  1 штрихкод  2 имя  3 ед  4 произв  5 цена
                           6 ост.общий  7 Микро  8 Берёз  9 группа  10 подгруппа
  8-кол  (старый до 18.07): 0 артикул  1 имя  2 ед  3 произв  4 цена
                           5 ост.общий  6 Микро  7 Берёз       (штрихкода и категорий нет)

Раскладка определяется по числу колонок. Внутри 11-кол — КАКАЯ из первых двух колонок
штрихкод: в образце 06.07 артикул↔штрихкод стояли наоборот, поэтому решаем ПО СОДЕРЖИМОМУ
(штрихкод = длинное числовое EAN/внутренний код; артикул — с дробями/буквами), а не по позиции.
Неизвестное число колонок → `ParserError` (парс падает ДО записи в БД — данные целы).

На выходе — единый список dict с теми же ключами, что и в таблице products (то, что раньше
собирал локальный `sync._prochitat`). У 8-кол `shtrihkod`/`gruppa`/`podgruppa` = "" (не падаем).
"""
import xlrd

from ..logger import logger


class ParserError(Exception):
    """Файл не удалось распарсить (неизвестная раскладка) — апдейт отменяется, БД цела."""


def _na(v) -> str:
    return str(v).strip().strip("\t").strip()


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _family_of(n: str) -> str:
    p = n.split()
    if not p:
        return "?"
    w = p[0].strip(",.")
    if w.upper() == "ШС" and len(p) > 1:
        return "ШС " + p[1].strip(",.")
    return w


def _pohozhe_na_shtrihkod(v: str) -> bool:
    """EAN/внутренний код: длинное (>=8) число без дробей/букв."""
    v = v.strip()
    return len(v) >= 8 and v.isdigit()


def _kolonka_shtrihkoda(sheet, proba: int = 100) -> int:
    """0 или 1 — какая из первых колонок штрихкод (по доле «числовых длинных» на пробе строк)."""
    n = min(proba, sheet.nrows)
    d0 = sum(_pohozhe_na_shtrihkod(_na(sheet.cell_value(r, 0))) for r in range(n))
    d1 = sum(_pohozhe_na_shtrihkod(_na(sheet.cell_value(r, 1))) for r in range(n))
    return 0 if d0 > d1 else 1


def opredelit_raskladku(sheet) -> str:
    """'11' | '8' по числу колонок; иначе ParserError."""
    if sheet.ncols == 11:
        return "11"
    if sheet.ncols == 8:
        return "8"
    raise ParserError(
        f"неизвестная раскладка: {sheet.ncols} колонок (ожидается 8 или 11) — импорт отменён"
    )


def prochitat(path: str) -> list[dict]:
    """Читает .xls прайса → список позиций (ключи как в products). Раскладка — сама."""
    book = xlrd.open_workbook(path, encoding_override="cp1251")
    sheet = book.sheet_by_index(0)
    layout = opredelit_raskladku(sheet)
    out = []

    if layout == "11":
        i_sh = _kolonka_shtrihkoda(sheet)
        i_art = 1 - i_sh
        logger.info(
            f"Раскладка 11-кол: артикул=col{i_art}, штрихкод=col{i_sh}, {sheet.nrows} строк"
        )
        for r in range(sheet.nrows):
            v = [_na(sheet.cell_value(r, c)) for c in range(11)]
            out.append({
                "artikul": v[i_art], "shtrihkod": v[i_sh], "imya": v[2], "edinica": v[3],
                "proizvoditel": v[4], "cena": _num(v[5]), "ostatok_obshiy": _num(v[6]),
                "ostatok_mikro": _num(v[7]), "ostatok_berez": _num(v[8]),
                "semeystvo": _family_of(v[2]), "gruppa": v[9], "podgruppa": v[10],
            })
        return out

    # layout == "8": штрихкода и категорий нет
    logger.info(f"Раскладка 8-кол (без штрихкода/категорий): {sheet.nrows} строк")
    for r in range(sheet.nrows):
        v = [_na(sheet.cell_value(r, c)) for c in range(8)]
        out.append({
            "artikul": v[0], "shtrihkod": "", "imya": v[1], "edinica": v[2],
            "proizvoditel": v[3], "cena": _num(v[4]), "ostatok_obshiy": _num(v[5]),
            "ostatok_mikro": _num(v[6]), "ostatok_berez": _num(v[7]),
            "semeystvo": _family_of(v[1]), "gruppa": "", "podgruppa": "",
        })
    return out
