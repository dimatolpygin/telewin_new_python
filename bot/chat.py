# -*- coding: utf-8 -*-
"""Оффлайн-диалог с ИИ-агентом в консоли (без Telegram). Этап 3.
Запуск: python -m bot.chat [--db] [--podgr]
Пустая строка или 'выход' — завершить.
"""
import asyncio
import sys

from .config import load_config
from .search.search import Poisk, load_products_json


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    use_db = "--db" in sys.argv
    use_podgr = "--podgr" in sys.argv
    cfg = load_config()

    pool = None
    gibrid = None
    if use_db:
        from .db import create_pool, zagruzit_vse_tovary
        from .search.vector import VectorKanal
        from .search.gibrid import Gibrid
        pool = await create_pool(cfg)
        tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
        poisk = Poisk(tovary)
        vk = VectorKanal(pool, cfg.pg.schema)
        gibrid = Gibrid(poisk, vk) if await vk.доступен() else None
    else:
        tovary = load_products_json()
        poisk = Poisk(tovary)

    from .ai.agent import run_agent
    режим = "гибрид (лексика ⊕ вектор)" if gibrid else "чистая лексика"
    print(f"Поиск готов: {poisk.размер_базы} товаров, {режим}. Пиши запрос (пустая строка — выход).\n")

    history: list[dict] = []
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_text = (await loop.run_in_executor(None, sys.stdin.readline)).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_text or user_text.lower() in ("выход", "exit", "quit"):
            break
        res = await run_agent(cfg.openrouter, poisk, history, user_text,
                              use_podgr=use_podgr, gibrid=gibrid)
        history = res.new_history[-12:]
        print(f"\nБОТ: {res.answer}")
        print(f"  [поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]\n")

    if pool is not None:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
