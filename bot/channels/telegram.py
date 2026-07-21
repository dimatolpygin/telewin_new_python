# -*- coding: utf-8 -*-
"""Адаптер канала Telegram (aiogram, polling) поверх ядра `Yadro` (этап 25).

Тонкий транспорт: принимает апдейты aiogram, зовёт `yadro.obrabotat(...)`, шлёт
ответ. Вся доменная логика (поиск, история, дата прайса, очередь) — в ядре.
"""
import time

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from ..config import Config
from ..core import Yadro
from ..logger import logger, log_deystvie_polzovatelya, log_otvet_bota
from ..texts import WELCOME, RESET_OK, ERROR_RETRY

CHANNEL = "telegram"


async def run_telegram(cfg: Config, yadro: Yadro) -> None:
    """Запустить Telegram-канал (polling). Блокирует до остановки dispatcher'а.
    Bot/Dispatcher создаются здесь, ядро приходит готовым и общим для всех каналов."""
    bot = Bot(cfg.bot_token)
    dp = Dispatcher()

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
            await yadro.sbros(CHANNEL, message.chat.id)
            await message.answer(RESET_OK)
            return
        if not text or text.startswith("/"):
            return
        await _obrabotat(message, text)

    async def _obrabotat(message: Message, user_text: str) -> None:
        chat_id = message.chat.id
        uname = message.from_user.username if message.from_user else None
        try:
            async def typing() -> None:
                await bot.send_chat_action(chat_id, "typing")

            t0 = time.time()
            res = await yadro.obrabotat(CHANNEL, chat_id, user_text, typing_cb=typing)
            await message.answer(res.answer)
            ms = int((time.time() - t0) * 1000)
            logger.info(
                f"🤖 [{CHANNEL}] → @{uname or '—'}: ответ за {ms}мс "
                f"[поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]"
            )
        except Exception as e:
            logger.error(f"[{CHANNEL}] Ошибка обработки сообщения: {e}")
            await message.answer(ERROR_RETRY)

    me = await bot.get_me()
    logger.info(f"Канал {CHANNEL}: бот @{me.username} на связи. Запуск polling…")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
