# -*- coding: utf-8 -*-
"""Импорт products.json -> Postgres telewin_test. Порт import.ts.
Запуск: python -m bot.import_price  (перед этим data/export_price.py)
"""
import asyncio
import json
import os
import sys

from .config import load_config
from .db import create_pool, COLS
from .logger import logger

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


async def main() -> None:
    cfg = load_config()
    schema = cfg.pg.schema
    tovary = json.load(open(os.path.join(_DATA, "products.json"), encoding="utf-8"))
    logger.info(f"Прочитано {len(tovary)} товаров из products.json")

    pool = await create_pool(cfg)
    try:
        async with pool.acquire() as con:
            # pgvector + столбец эмбеддингов заводим в коде (воспроизводимый init в
            # чистом volume, этап 30): раньше делались вручную через psql/pg-meta.
            # embedding halfvec(3072) — NULL до расчёта (bot.embed_index); HNSW строит он же.
            await con.execute("create extension if not exists vector")
            await con.execute(f"create schema if not exists {schema}")
            await con.execute(f"drop table if exists {schema}.products")
            await con.execute(f"""
                create table {schema}.products (
                    id             serial primary key,
                    artikul        text,
                    shtrihkod      text,
                    imya           text not null,
                    edinica        text,
                    proizvoditel   text,
                    cena           numeric,
                    ostatok_obshiy numeric,
                    ostatok_mikro  numeric,
                    ostatok_berez  numeric,
                    semeystvo      text,
                    gruppa         text,
                    podgruppa      text,
                    embedding      halfvec(3072)
                )
            """)

            # пакетная вставка через copy_records_to_table (быстро и без склейки SQL).
            # id НЕ пишем — serial генерит сам (в products.json поля id нет; COPY с
            # явным NULL в serial падал на чистом volume — этап 30).
            copy_cols = [c for c in COLS if c != "id"]
            records = [tuple(t.get(c) for c in copy_cols) for t in tovary]
            await con.copy_records_to_table(
                "products", records=records, columns=copy_cols, schema_name=schema
            )

            await con.execute(f"create index on {schema}.products (artikul)")
            await con.execute(f"create index on {schema}.products (shtrihkod)")
            await con.execute(f"create index on {schema}.products (semeystvo)")

            n = await con.fetchval(f"select count(*) from {schema}.products")
            n_ean = await con.fetchval(
                f"select count(*) from {schema}.products where shtrihkod is not null and shtrihkod <> ''"
            )
            logger.info(f"Импортировано в {schema}.products: {n} строк")
            logger.info(f"  со штрихкодом: {n_ean} ({n_ean / n * 100:.0f}%)")
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Ошибка импорта: {e}")
        sys.exit(1)
