# -*- coding: utf-8 -*-
"""Точка входа: Telegram-бот (aiogram, polling) поверх ИИ-поиска. Порт index.ts.
Запуск: python -m bot.main
"""
import asyncio
import collections
import time

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from .config import load_config
from .logger import logger, log_deystvie_polzovatelya, log_otvet_bota
from .search.search import Poisk
from .session import Sessions
from .ai.agent import run_agent

WELCOME = (
    "Здравствуйте! Я помогу найти товар в магазине «Домашний мастер» — подскажу цену и наличие.\n\n"
    "Спрашивайте обычными словами, например:\n"
    "• нужны гвозди сотка\n"
    "• саморезы для гипсокартона\n"
    "• чем прикрутить профлист\n\n"
    "Команда /reset — начать диалог заново."
)


async def main() -> None:
    cfg = load_config()

    # Товары из Postgres (как боевой TS-бот). Поиск держит всё в памяти процесса.
    from .db import create_pool, zagruzit_vse_tovary
    pool = await create_pool(cfg)
    tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
    await pool.close()
    poisk = Poisk(tovary)
    logger.info(f"Поиск готов: {poisk.размер_базы} товаров в базе")

    sessions = Sessions(cfg)
    bot = Bot(cfg.bot_token)
    dp = Dispatcher()

    # Очередь на чат: сообщения одного пользователя обрабатываем строго по очереди,
    # иначе два быстрых подряд читают/пишут одну историю в Redis (гонка) и ответы путаются.
    # asyncio.Lock обслуживает ожидающих в порядке FIFO — это и есть очередь.
    zamki: dict[int, asyncio.Lock] = collections.defaultdict(asyncio.Lock)

    # Лог каждого действия пользователя (требование CLAUDE.md)
    @dp.message()
    async def log_and_route(message: Message) -> None:
        u = message.from_user
        text = message.text or "(не текст)"
        log_deystvie_polzovatelya(u.username if u else None, u.id if u else None,
                                  u.first_name if u else None, text)
        await _route(message)

    async def _route(message: Message) -> None:
        text = message.text or ""
        if text.startswith("/start"):
            await message.answer(WELCOME)
            log_otvet_bota(message.from_user.username if message.from_user else None, "приветствие")
            return
        if text.startswith("/reset"):
            await sessions.sbros(message.chat.id)
            await message.answer("Диалог сброшен. Что ищем?")
            return
        if not text or text.startswith("/"):
            return
        await _obrabotat_zapros(message, text)

    async def _obrabotat_zapros(message: Message, user_text: str) -> None:
        chat_id = message.chat.id
        uname = message.from_user.username if message.from_user else None
        async with zamki[chat_id]:  # строго по очереди в рамках чата
            try:
                await bot.send_chat_action(chat_id, "typing")
                history = await sessions.zagruzit(chat_id)
                t0 = time.time()
                res = await run_agent(cfg.openrouter, poisk, history, user_text)
                await sessions.sohranit(chat_id, res.new_history)
                await message.answer(res.answer)
                ms = int((time.time() - t0) * 1000)
                logger.info(
                    f"🤖 → @{uname or '—'}: ответ за {ms}мс "
                    f"[поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]"
                )
            except Exception as e:
                logger.error(f"Ошибка обработки сообщения: {e}")
                await message.answer("Секунду, связь подвисла — повторите запрос, пожалуйста.")

    me = await bot.get_me()
    logger.info(f"Бот @{me.username} на связи. Запуск polling…")
    try:
        await dp.start_polling(bot)
    finally:
        await sessions.zakryt()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
