# -*- coding: utf-8 -*-
"""Слой данных Postgres (asyncpg). Порт db.ts.
Товары возвращаются как dict с ключами формата products.json — чтобы поиск
(`bot.search`) работал с БД и с json без переходника.
"""
import asyncpg

from .config import Config

# Порядок колонок таблицы telewin_test.products.
# `id` — первичный ключ; нужен, чтобы связать лексический и векторный каналы при RRF
# (этап 10). Для поиска по json (без id) не мешает — лишний ключ в dict игнорируется.
COLS = [
    "id",
    "artikul", "shtrihkod", "imya", "edinica", "proizvoditel", "cena",
    "ostatok_obshiy", "ostatok_mikro", "ostatok_berez",
    "semeystvo", "gruppa", "podgruppa",
]


async def create_pool(cfg: Config) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=cfg.pg.host, port=cfg.pg.port, user=cfg.pg.user,
        password=cfg.pg.password, database=cfg.pg.database, min_size=1, max_size=5,
    )


async def zagruzit_vse_tovary(pool: asyncpg.Pool, schema: str) -> list[dict]:
    """Все товары из БД как список dict (ключи как в products.json)."""
    rows = await pool.fetch(f"select {', '.join(COLS)} from {schema}.products")
    out = []
    for r in rows:
        d = dict(r)
        # numeric приходит как Decimal — приводим к float для поиска/ответа
        for k in ("cena", "ostatok_obshiy", "ostatok_mikro", "ostatok_berez"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        out.append(d)
    return out
