# -*- coding: utf-8 -*-
"""Каналонезависимое ядро диалога (этап 25).

Раньше эта логика жила прямо в `main.py` и была сцеплена с Telegram. Теперь она
здесь и принимает/отдаёт чистый текст — поверх неё встают адаптеры любого канала
(Telegram, VK, MAX). Ядро НЕ знает про транспорт: только `(канал, chat_key, текст)`.

Гарантии, перенесённые из `main.py`:
- **очередь-замок на `(канал, чат)`**: сообщения одного собеседника в одном канале
  обрабатываются строго по очереди (иначе два быстрых подряд читают/пишут одну
  историю в Redis и ответы путаются). `asyncio.Lock` обслуживает FIFO — это очередь;
- дата актуальности прайса читается на КАЖДЫЙ запрос (дёшево, одна строка), чтобы
  бот подхватывал ежедневное обновление без перезапуска (этап 20).
"""
import asyncio
import collections
from typing import Awaitable, Callable, Optional

from .config import Config
from .ai.agent import run_agent, AgentResult, ДАТА_ПРАЙСА
from .search.search import Poisk
from .session import Sessions


class Yadro:
    """Общий движок диалога. Один экземпляр на процесс — его делят все каналы,
    поэтому индекс поиска (`poisk`/`gibrid`) и пул БД грузятся ровно один раз."""

    def __init__(self, cfg: Config, poisk: Poisk, sessions: Sessions,
                 *, gibrid=None, pool=None) -> None:
        self._cfg = cfg
        self._poisk = poisk
        self._sessions = sessions
        self._gibrid = gibrid
        self._pool = pool
        # Замки на (канал, chat_key). defaultdict — ключ создаётся лениво при первом
        # обращении. str(chat_key) — id каналов разного типа приводим к строке.
        self._zamki: dict[tuple[str, str], asyncio.Lock] = collections.defaultdict(asyncio.Lock)
        # Блок про адреса/часы точек (этап 36-Б) — статичен, собираем один раз из .env.
        from .ai.agent import sobrat_tochki
        self._tochki = sobrat_tochki(cfg.shop_addr_mikro, cfg.shop_addr_berez, cfg.shop_hours)

    @property
    def pool(self):
        """Пул БД (для выгрузки отчёта по диалогам, этап 38). Может быть None."""
        return self._pool

    async def obrabotat(
        self, channel: str, chat_key, user_text: str, *,
        typing_cb: Optional[Callable[[], Awaitable[None]]] = None,
        imya: Optional[str] = None, nik: Optional[str] = None,
    ) -> AgentResult:
        """Обработать запрос пользователя канала `channel`, чат `chat_key`.

        `typing_cb` — необязательный колбэк «показать, что бот печатает» (в TG это
        send_chat_action). Вызывается под замком, перед долгой работой агента.
        `imya`/`nik` — что канал знает о собеседнике; идут только в журнал диалогов
        (этап 38), на поиск/ответ не влияют.
        Возвращает `AgentResult` (ответ + метаданные поиска для логов адаптера).
        """
        kluch = (channel, str(chat_key))
        async with self._zamki[kluch]:  # строго по очереди в рамках (канал, чат)
            if typing_cb is not None:
                await typing_cb()
            history = await self._sessions.zagruzit(channel, chat_key)
            data_prajsa = await self._data_prajsa()
            res = await run_agent(
                self._cfg.openrouter, self._poisk, history, user_text,
                gibrid=self._gibrid, data_prajsa=data_prajsa, tochki=self._tochki,
            )
            await self._sessions.sohranit(channel, chat_key, res.new_history)
        # журнал — ПОСЛЕ снятия замка (не держим очередь на записи отчётной таблицы);
        # best-effort внутри zapisat, ошибки не всплывают
        await self._zhurnal(channel, chat_key, user_text, res, imya, nik)
        return res

    async def _zhurnal(self, channel, chat_key, user_text, res, imya, nik) -> None:
        """Best-effort запись хода диалога в таблицу отчёта (этап 38)."""
        from .dialog_log import zapisat
        naydeno = res.naydeno if res.zaprosy_poiska else -1  # -1 = бот не искал за ход
        await zapisat(self._pool, self._cfg.pg.schema, channel, chat_key,
                      user_text, res.answer, imya=imya, nik=nik,
                      iskal=res.zaprosy_poiska, naydeno=naydeno)

    async def sbros(self, channel: str, chat_key) -> None:
        """Сброс истории диалога (команда /reset и её эквиваленты в каналах)."""
        await self._sessions.sbros(channel, chat_key)

    async def _data_prajsa(self) -> str:
        """Дата актуальности прайса из `price_meta`; без БД — статический fallback."""
        if self._pool is None:
            return ДАТА_ПРАЙСА
        from .update.meta import zagruzit_datu
        return await zagruzit_datu(self._pool, self._cfg.pg.schema)
