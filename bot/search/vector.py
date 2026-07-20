# -*- coding: utf-8 -*-
"""Векторный канал поиска (этап 10): эмбеддинг запроса → kNN по pgvector (halfvec,
косинус). Ловит композицию и открытые синонимы, которых нет в словаре домена
(«болгарка»→УШМ, «наждачка»→шлифовальная лента). Слияние с лексикой — в gibrid.py.
"""
import asyncpg

from ..ai.embeddings import в_литерал_вектора, посчитать_эмбеддинг


class VectorKanal:
    def __init__(self, pool: asyncpg.Pool, schema: str):
        self.pool = pool
        self.schema = schema

    async def доступен(self) -> bool:
        """Есть ли хоть один посчитанный вектор (иначе гибрид сводится к лексике)."""
        n = await self.pool.fetchval(
            f"select count(embedding) from {self.schema}.products"
        )
        return bool(n)

    async def knn(self, запрос: str, limit: int = 50) -> list[tuple[int, float]]:
        """kNN по эмбеддингу запроса. Возвращает [(id, sim)] по убыванию похожести.

        sim = 1 − косинусное расстояние (1.0 — идентичны). Жёстких порогов не ставим:
        отбор кандидатов, ранжирование и слияние — задача RRF в gibrid.py.
        """
        вект = await посчитать_эмбеддинг(запрос)
        лит = в_литерал_вектора(вект)
        rows = await self.pool.fetch(
            f"select id, 1 - (embedding <=> $1::halfvec) as sim "
            f"from {self.schema}.products where embedding is not null "
            f"order by embedding <=> $1::halfvec limit $2",
            лит, limit,
        )
        return [(r["id"], float(r["sim"])) for r in rows]
