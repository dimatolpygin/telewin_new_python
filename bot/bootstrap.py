# -*- coding: utf-8 -*-
"""Сборка общих зависимостей процесса (БД, поиск, ядро) — один раз на процесс.

Вынесено из `main.py` (этап 27), чтобы любой канал мог подняться на том же ядре:
индекс поиска (11 864 товара) грузится единожды и делится всеми каналами. На
этапе 29 (оркестрация) это же используется для запуска TG+VK+MAX в одном процессе.
"""
from .config import Config
from .logger import logger
from .core import Yadro
from .session import Sessions
from .search.search import Poisk


async def sozdat_yadro(cfg: Config) -> tuple[Yadro, object, Sessions]:
    """Поднять пул БД, загрузить товары в память, собрать ядро `Yadro`.
    Возвращает (ядро, пул, сессии) — пул/сессии закрывает вызывающий на выходе."""
    from .db import create_pool, zagruzit_vse_tovary
    from .search.vector import VectorKanal
    from .search.gibrid import Gibrid

    pool = await create_pool(cfg)
    tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
    poisk = Poisk(tovary)
    logger.info(f"Поиск готов: {poisk.размер_базы} товаров в базе")

    # Гибрид включаем, только если эмбеддинги посчитаны (иначе — чистая лексика).
    vk = VectorKanal(pool, cfg.pg.schema)
    gibrid = Gibrid(poisk, vk) if await vk.доступен() else None
    logger.info("Поиск: гибрид (лексика ⊕ вектор, RRF)" if gibrid
                else "Поиск: чистая лексика (эмбеддинги не посчитаны)")

    sessions = Sessions(cfg)
    yadro = Yadro(cfg, poisk, sessions, gibrid=gibrid, pool=pool)
    return yadro, pool, sessions
