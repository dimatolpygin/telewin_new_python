# -*- coding: utf-8 -*-
"""Точка входа процесса. Собирает общие зависимости (БД, поиск, ядро) и запускает
каналы поверх единого ядра `Yadro`.

Этап 25: пока поднимается только Telegram-адаптер. Каналы VK/MAX (этапы 27/28) и
их совместная оркестрация (этап 29) встают на то же ядро — индекс поиска грузится
один раз и общий для всех каналов.
Запуск: python -m bot.main
"""
import asyncio

from .config import load_config
from .logger import logger
from .core import Yadro
from .session import Sessions
from .search.search import Poisk
from .channels.telegram import run_telegram


async def main() -> None:
    cfg = load_config()

    # Товары из Postgres (как боевой TS-бот). Поиск держит всё в памяти процесса.
    # Пул НЕ закрываем по ходу — векторный канал (этап 10) ходит в pgvector на каждый запрос.
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
    logger.info("Поиск: гибрид (лексика ⊕ вектор, RRF)" if gibrid else "Поиск: чистая лексика (эмбеддинги не посчитаны)")

    sessions = Sessions(cfg)
    yadro = Yadro(cfg, poisk, sessions, gibrid=gibrid, pool=pool)

    try:
        await run_telegram(cfg, yadro)
    finally:
        await sessions.zakryt()
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
