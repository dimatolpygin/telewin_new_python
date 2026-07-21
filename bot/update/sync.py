# -*- coding: utf-8 -*-
"""Этап 19 — инкрементальный апдейт прайса в Postgres (UPSERT-diff вместо DROP).

Зачем: `import_price.py` делает DROP+COPY и стирает колонку `embedding` → любой апдейт
превращается в полный пересчёт векторов (~35 мин + деньги OpenRouter). Здесь БД обновляется
инкрементально:
  - ФАКТЫ (цена/остаток/производитель/единица/категории) — обновляются ВСЕГДА;
  - `embedding` обнуляется ТОЛЬКО при смене обогащающих полей (имя/семейство/группа/подгруппа),
    т.к. вектор считается по обогащённому имени (`search.obogashenie`);
  - новые позиции → INSERT (embedding NULL — досчитает re-embed этапа 20);
  - исчезнувшие из прайса → DELETE (БД = зеркало последнего прайса).

Ключ сопоставления «та же позиция» — ШТРИХКОД: в прайсе он 100% заполнен и уникален
(даже без реального EAN Фора кладёт внутренний код 2000…). Артикул НЕ ключ — Фора
переиспользует коды под разные товары (200 дублей, 188 с разными именами). Fallback для
строк без штрихкода — (артикул, имя); сейчас таких 0, заложено на будущее.

Парсер файла пока локальный, под 11-колоночную раскладку 18.07 (та же у фикстура). Общий
парсер с АВТООПРЕДЕЛЕНИЕМ раскладки (8 vs 11 колонок) — этап 18, тогда `_prochitat` уедет туда.

Запуск: python -m bot.update.sync <путь_к_xls>
        python -m bot.update.sync <путь> --apply-embed   # + инкрементальный re-embed (этап 20)
"""
import asyncio
import os
import sys

import xlrd

from ..config import load_config
from ..db import create_pool
from ..logger import logger
from . import meta

# Раскладка 11-кол (0-based): 0 артикул 1 штрихкод 2 имя 3 ед 4 произв
#   5 цена 6 ост.общий 7 ост.Микро 8 ост.Берёз 9 группа 10 подгруппа
_ENRICH = ("imya", "semeystvo", "gruppa", "podgruppa")  # смена любого → embedding=NULL
_FACTS = ("cena", "ostatok_obshiy", "ostatok_mikro", "ostatok_berez", "proizvoditel", "edinica")


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


def _prochitat(path: str) -> list[dict]:
    """Читает .xls (11-кол раскладка) → список позиций (ключи как в products.json).
    Раскладка захардкожена; автоопределение (8 vs 11) — этап 18."""
    b = xlrd.open_workbook(path, encoding_override="cp1251")
    sh = b.sheet_by_index(0)
    out = []
    for r in range(sh.nrows):
        v = [_na(sh.cell_value(r, c)) for c in range(11)]
        out.append({
            "artikul": v[0], "shtrihkod": v[1], "imya": v[2], "edinica": v[3],
            "proizvoditel": v[4], "cena": _num(v[5]), "ostatok_obshiy": _num(v[6]),
            "ostatok_mikro": _num(v[7]), "ostatok_berez": _num(v[8]),
            "semeystvo": _family_of(v[2]), "gruppa": v[9], "podgruppa": v[10],
        })
    return out


def _kl(t: dict):
    """Ключ сопоставления позиции между прайсами."""
    sh = (t.get("shtrihkod") or "").strip()
    if sh and sh != "0":
        return ("sh", sh)
    return ("ai", (t.get("artikul") or "").strip(), (t.get("imya") or "").strip())


def _po_klyucham(items: list[dict], chto: str) -> dict:
    """Индекс ключ→позиция; при дубле ключа оставляет первую, warning в лог."""
    d = {}
    for t in items:
        k = _kl(t)
        if k in d:
            logger.warning(f"дубль ключа в {chto}: {k} — оставлена первая строка")
            continue
        d[k] = t
    return d


def _izmenilos(a: dict, b: dict, polya) -> bool:
    for f in polya:
        av, bv = a.get(f), b.get(f)
        # числа сравниваем как float, остальное как строку
        if f in _FACTS and f != "proizvoditel" and f != "edinica":
            if (av is None) != (bv is None):
                return True
            if av is not None and abs(float(av) - float(bv)) > 1e-9:
                return True
        else:
            if str(av or "").strip() != str(bv or "").strip():
                return True
    return False


async def sync(path: str, apply_embed: bool = False, monitor: bool = False) -> dict:
    cfg = load_config()
    schema = cfg.pg.schema

    novye_vse = _prochitat(path)
    logger.info(f"Прочитано из файла: {len(novye_vse)} строк — {os.path.basename(path)}")
    new = _po_klyucham(novye_vse, "файле")

    pool = await create_pool(cfg)
    itog = {}
    try:
        async with pool.acquire() as con:
            rows = await con.fetch(
                f"select id, artikul, shtrihkod, imya, edinica, proizvoditel, cena, "
                f"ostatok_obshiy, ostatok_mikro, ostatok_berez, semeystvo, gruppa, podgruppa "
                f"from {schema}.products"
            )
            # numeric приходит как Decimal — привести к float для сравнения фактов
            db_rows = []
            for r in rows:
                d = dict(r)
                for kf in ("cena", "ostatok_obshiy", "ostatok_mikro", "ostatok_berez"):
                    if d.get(kf) is not None:
                        d[kf] = float(d[kf])
                db_rows.append(d)
            db = _po_klyucham(db_rows, "БД")
            logger.info(f"В БД сейчас: {len(db)} позиций")

            to_insert, upd_fact, upd_rename, skip = [], [], [], 0
            for k, t in new.items():
                cur = db.get(k)
                if cur is None:
                    to_insert.append(t)                  # новая позиция
                elif _izmenilos(t, cur, _ENRICH):        # сменилось обогащающее → re-embed
                    upd_rename.append((cur["id"], t))
                elif _izmenilos(t, cur, _FACTS):         # изменились только факты
                    upd_fact.append((cur["id"], t))
                else:
                    skip += 1                            # ничего не изменилось — не трогаем
            to_delete = [cur["id"] for k, cur in db.items() if k not in new]

            async with con.transaction():
                # 1) факты (embedding не трогаем)
                if upd_fact:
                    await con.executemany(
                        f"update {schema}.products set cena=$2, ostatok_obshiy=$3, "
                        f"ostatok_mikro=$4, ostatok_berez=$5, proizvoditel=$6, "
                        f"edinica=$7, gruppa=$8, podgruppa=$9 where id=$1",
                        [(i, t["cena"], t["ostatok_obshiy"], t["ostatok_mikro"],
                          t["ostatok_berez"], t["proizvoditel"], t["edinica"],
                          t["gruppa"], t["podgruppa"]) for i, t in upd_fact],
                    )
                # 2) смена имени/категории → обновить всё + обнулить вектор
                if upd_rename:
                    await con.executemany(
                        f"update {schema}.products set imya=$2, semeystvo=$3, gruppa=$4, "
                        f"podgruppa=$5, cena=$6, ostatok_obshiy=$7, ostatok_mikro=$8, "
                        f"ostatok_berez=$9, proizvoditel=$10, edinica=$11, "
                        f"embedding=NULL where id=$1",
                        [(i, t["imya"], t["semeystvo"], t["gruppa"], t["podgruppa"],
                          t["cena"], t["ostatok_obshiy"], t["ostatok_mikro"],
                          t["ostatok_berez"], t["proizvoditel"], t["edinica"])
                         for i, t in upd_rename],
                    )
                # 3) новые (embedding NULL — досчитает этап 20)
                if to_insert:
                    await con.executemany(
                        f"insert into {schema}.products (artikul, shtrihkod, imya, edinica, "
                        f"proizvoditel, cena, ostatok_obshiy, ostatok_mikro, ostatok_berez, "
                        f"semeystvo, gruppa, podgruppa) "
                        f"values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                        [(t["artikul"], t["shtrihkod"], t["imya"], t["edinica"],
                          t["proizvoditel"], t["cena"], t["ostatok_obshiy"],
                          t["ostatok_mikro"], t["ostatok_berez"], t["semeystvo"],
                          t["gruppa"], t["podgruppa"]) for t in to_insert],
                    )
                # 4) исчезнувшие
                if to_delete:
                    await con.execute(
                        f"delete from {schema}.products where id = any($1::int[])", to_delete
                    )

            # дата актуальности прайса (этап 20): из имени файла / mtime
            price_date = meta.data_iz_faila(path)
            await meta.zapisat(con, schema, price_date, os.path.basename(path))

            n = await con.fetchval(f"select count(*) from {schema}.products")
            emb = await con.fetchval(f"select count(embedding) from {schema}.products")
            itog = {"insert": len(to_insert), "upd_fact": len(upd_fact),
                    "upd_rename": len(upd_rename), "delete": len(to_delete),
                    "skip": skip, "rows": n, "embedding": emb,
                    "price_date": price_date.strftime("%d.%m.%Y %H:%M") if price_date else None}
    finally:
        await pool.close()

    logger.info(
        f"DIFF: обновлено фактов {itog['upd_fact']}, смен имени {itog['upd_rename']} (→re-embed), "
        f"добавлено {itog['insert']}, удалено {itog['delete']}, без изменений {itog['skip']}"
    )
    logger.info(f"ИТОГ: строк {itog['rows']}, с embedding {itog['embedding']} "
                f"(без вектора {itog['rows'] - itog['embedding']}); "
                f"дата прайса {itog['price_date']}")

    if apply_embed:
        logger.info("Инкрементальный re-embed (embed_index, только embedding IS NULL)…")
        from ..embed_index import main as embed_main
        await embed_main()

    if monitor:
        from . import monitor as _mon
        pool2 = await create_pool(cfg)
        try:
            itog["novelty"] = await _mon.proverit(pool2, schema)
        finally:
            await pool2.close()

    return itog


def _main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        logger.error("Укажи путь: python -m bot.update.sync <файл> [--apply-embed] [--monitor]")
        sys.exit(1)
    path = args[0]
    if not os.path.isfile(path):
        logger.error(f"Файл не найден: {path}")
        sys.exit(1)
    asyncio.run(sync(path, apply_embed="--apply-embed" in sys.argv,
                     monitor="--monitor" in sys.argv))


if __name__ == "__main__":
    _main()
