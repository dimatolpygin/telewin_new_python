# -*- coding: utf-8 -*-
"""Индексация эмбеддингов товаров (этап 10). Считает вектор обогащённого имени для
каждой позиции прайса и кладёт в столбец `embedding halfvec(3072)`, затем строит HNSW.

Запуск:  python -m bot.embed_index            # только пустые (embedding IS NULL)
         python -m bot.embed_index --all      # пересчитать все
         python -m bot.embed_index --index     # только (пере)построить HNSW-индекс
"""
import asyncio
import sys

import asyncpg

from .ai.embeddings import в_литерал_вектора, мерность_эмбеддинга, модель_эмбеддингов, посчитать_эмбеддинги
from .config import load_config
from .logger import logger
from .search.obogashenie import обогатить

_HNSW = "products_embedding_hnsw"


async def _создать_индекс(pool: asyncpg.Pool, schema: str) -> None:
    logger.info("Строю HNSW-индекс (halfvec_cosine_ops)…")
    async with pool.acquire() as c:
        await c.execute(
            f"CREATE INDEX IF NOT EXISTS {_HNSW} ON {schema}.products "
            f"USING hnsw (embedding halfvec_cosine_ops)"
        )
    logger.info("HNSW-индекс готов.")


async def main() -> None:
    cfg = load_config()
    только_индекс = "--index" in sys.argv
    все = "--all" in sys.argv

    pool = await asyncpg.create_pool(
        host=cfg.pg.host, port=cfg.pg.port, user=cfg.pg.user,
        password=cfg.pg.password, database=cfg.pg.database, min_size=1, max_size=2,
    )
    schema = cfg.pg.schema
    try:
        if только_индекс:
            await _создать_индекс(pool, schema)
            return

        усл = "" if все else "where embedding is null"
        rows = await pool.fetch(
            f"select id, imya, semeystvo, gruppa, podgruppa from {schema}.products {усл} order by id"
        )
        if not rows:
            logger.info("Нечего считать — все позиции уже с эмбеддингом (или --all для пересчёта).")
            await _создать_индекс(pool, schema)
            return

        logger.info(
            f"Модель {модель_эмбеддингов()} (dim {мерность_эмбеддинга()}). "
            f"Считаю эмбеддинги для {len(rows)} позиций…"
        )
        тексты = [обогатить(dict(r)) for r in rows]
        ids = [r["id"] for r in rows]

        def прогресс(готово: int, всего: int) -> None:
            if готово % (64 * 10) == 0 or готово == всего:
                logger.info(f"  эмбеддинги: {готово}/{всего}")

        векторы = await посчитать_эмбеддинги(тексты, on_progress=прогресс)

        logger.info("Записываю векторы в БД…")
        params = [(в_литерал_вектора(v), i) for v, i in zip(векторы, ids)]
        async with pool.acquire() as c:
            await c.executemany(
                f"update {schema}.products set embedding = $1::halfvec where id = $2", params
            )

        покрыто = await pool.fetchval(
            f"select count(embedding) from {schema}.products"
        )
        всего = await pool.fetchval(f"select count(*) from {schema}.products")
        logger.info(f"Готово: {покрыто}/{всего} позиций с эмбеддингом.")
        await _создать_индекс(pool, schema)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
