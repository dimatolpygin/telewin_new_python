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

    if use_db:
        from .db import create_pool, zagruzit_vse_tovary
        pool = await create_pool(cfg)
        tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
        await pool.close()
    else:
        tovary = load_products_json()

    poisk = Poisk(tovary)
    from .ai.agent import run_agent
    print(f"Поиск готов: {poisk.размер_базы} товаров. Пиши запрос (пустая строка — выход).\n")

    history: list[dict] = []
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_text = (await loop.run_in_executor(None, sys.stdin.readline)).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_text or user_text.lower() in ("выход", "exit", "quit"):
            break
        res = await run_agent(cfg.openrouter, poisk, history, user_text, use_podgr=use_podgr)
        history = res.new_history[-12:]
        print(f"\nБОТ: {res.answer}")
        print(f"  [поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]\n")


if __name__ == "__main__":
    asyncio.run(main())
