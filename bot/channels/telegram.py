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
from ..keyboards import tg_klaviatura, vk_nazhata_svyaz
from ..logger import (logger, nachat_zapros, log_vhodyashchee,
                      log_ishodyashchee, log_oshibka)
from ..texts import WELCOME, RESET_OK, ERROR_RETRY, kontakt

CHANNEL = "telegram"


async def run_telegram(cfg: Config, yadro: Yadro) -> None:
    """Запустить Telegram-канал (polling). Блокирует до остановки dispatcher'а.
    Bot/Dispatcher создаются здесь, ядро приходит готовым и общим для всех каналов."""
    bot = Bot(cfg.bot_token)
    dp = Dispatcher()

    # Лог каждого действия пользователя (требование CLAUDE.md). Контекст запроса
    # (канал + новый request-id) открываем здесь — он сшивает всю цепочку логов
    # (вход → tool-call → вызов ИИ → выход) одним id для этого сообщения.
    @dp.message()
    async def log_and_route(message: Message) -> None:
        with nachat_zapros(CHANNEL):
            u = message.from_user
            text = message.text or "(не текст)"
            log_vhodyashchee(u.username if u else None, u.id if u else None,
                             u.first_name if u else None, text)
            await _route(message)

    async def _route(message: Message) -> None:
        text = message.text or ""
        # кнопка связи (этап 35): в TG нажатие приходит обычным текстом с подписью
        # кнопки, поэтому ловим по тексту; поиск и ИИ не трогаем
        if vk_nazhata_svyaz(text, None):
            await message.answer(kontakt(cfg.shop_phone), reply_markup=tg_klaviatura())
            log_ishodyashchee(message.from_user.username if message.from_user else None,
                              "телефон магазина (кнопка связи)")
            return
        # кодовое слово выгрузки Excel-отчёта (этап 38) — секрет-гейт из .env;
        # ловим ДО /start и обычной обработки, чтобы не ушло в поиск
        if cfg.stats_code and text.strip() == cfg.stats_code:
            await _otchet(message)
            return
        if text.startswith("/start"):
            await message.answer(WELCOME, reply_markup=tg_klaviatura())
            log_ishodyashchee(message.from_user.username if message.from_user else None, "приветствие")
            return
        if text.startswith("/reset"):
            await yadro.sbros(CHANNEL, message.chat.id)
            await message.answer(RESET_OK, reply_markup=tg_klaviatura())
            return
        if not text or text.startswith("/"):
            return
        await _obrabotat(message, text)

    async def _otchet(message: Message) -> None:
        """Собрать .xlsx-отчёт по диалогам и прислать документом (этап 38)."""
        from aiogram.types import BufferedInputFile
        from ..dialog_log import postroit_otchet_xlsx
        uname = message.from_user.username if message.from_user else None
        try:
            data = await postroit_otchet_xlsx(yadro.pool, cfg.pg.schema)
            imya_fajla = "Отчёт_диалоги.xlsx"
            await message.answer_document(BufferedInputFile(data, imya_fajla),
                                          caption="Отчёт по диалогам бота.")
            log_ishodyashchee(uname, f"отчёт по диалогам ({len(data)} Б)")
        except Exception:
            log_oshibka("Не удалось собрать отчёт по диалогам")
            await message.answer("Не получилось собрать отчёт — данные ещё копятся "
                                 "или база недоступна. Попробуйте позже.")

    async def _obrabotat(message: Message, user_text: str) -> None:
        chat_id = message.chat.id
        u = message.from_user
        uname = u.username if u else None
        try:
            async def typing() -> None:
                await bot.send_chat_action(chat_id, "typing")

            t0 = time.time()
            res = await yadro.obrabotat(CHANNEL, chat_id, user_text, typing_cb=typing,
                                        imya=(u.first_name if u else None), nik=uname)
            await message.answer(res.answer, reply_markup=tg_klaviatura())
            ms = int((time.time() - t0) * 1000)
            log_ishodyashchee(uname, res.answer, ms=ms,
                              meta=f"[поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]")
        except Exception:
            log_oshibka("Ошибка обработки сообщения", zapros=user_text)
            await message.answer(ERROR_RETRY)

    me = await bot.get_me()
    logger.info(f"Канал {CHANNEL}: бот @{me.username} на связи. Запуск polling…")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
