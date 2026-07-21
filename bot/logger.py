# -*- coding: utf-8 -*-
"""Логирование на русском со сквозным request-id (этап 26).

Требование CLAUDE.md/ТЗ: логировать «каждую щель» — входящее/исходящее сообщение,
каждый tool-call (имя+аргументы+результат), каждый вызов ИИ (модель, латентность,
токены; при отладке — промпт/ответ), ошибки с контекстом. И всё это должно
сшиваться ОДНИМ request-id по всему пути обработки одного сообщения.

Механика: `contextvars` держат текущие канал и request-id для asyncio-задачи
(каждое входящее сообщение обрабатывается в своей задаче — значения не смешиваются).
Фильтр подмешивает их в КАЖДУЮ запись лога, поэтому любой `logger.info(...)`
где угодно по тракту автоматически несёт `[канал rid]` — не нужно таскать id
параметром через core → agent → openrouter.

`LOG_LEVEL=debug` (env) поднимает уровень и включает промпт/ответ ИИ в логах;
на `info` (по умолчанию) промпты не пишутся (не текут в прод-логи).
"""
import contextlib
import contextvars
import logging
import os
import sys
import uuid

_FORMAT = "%(asctime)s  %(levelname)-5s  [%(channel)s %(rid)s]  %(message)s"
_DATEFMT = "%d.%m.%Y %H:%M:%S"

# Сквозной контекст одного запроса. default — прочерк (для строк вне обработки
# сообщения: старт процесса, загрузка поиска и т.п.).
_channel: contextvars.ContextVar[str] = contextvars.ContextVar("channel", default="—")
_rid: contextvars.ContextVar[str] = contextvars.ContextVar("rid", default="—")


class _KontekstFilter(logging.Filter):
    """Подмешивает канал и request-id в каждую запись — их читает форматтер."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.channel = _channel.get()
        record.rid = _rid.get()
        return True


def _uroven() -> int:
    return logging.DEBUG if os.environ.get("LOG_LEVEL", "").lower() == "debug" else logging.INFO


def _setup() -> logging.Logger:
    lg = logging.getLogger("telewin")
    if lg.handlers:
        return lg
    # На Windows консоль по умолчанию CP866 -> кириллица кракозябрами.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    lg.setLevel(_uroven())
    h = logging.StreamHandler(sys.stdout)  # stdout — основной сток (для Docker)
    h.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    h.addFilter(_KontekstFilter())
    lg.addHandler(h)
    lg.propagate = False
    return lg


logger = _setup()

# debug-режим включает промпт/ответ ИИ в лог вызова модели
DEBUG_LOGS = _uroven() == logging.DEBUG


@contextlib.contextmanager
def nachat_zapros(channel: str):
    """Открыть контекст обработки одного входящего сообщения: зафиксировать канал
    и сгенерировать новый request-id. Всё, что логируется внутри (включая awaits
    той же задачи — core/agent/openrouter), несёт этот rid. Возвращает rid."""
    rid = uuid.uuid4().hex[:8]
    t_ch = _channel.set(channel)
    t_rid = _rid.set(rid)
    try:
        yield rid
    finally:
        _channel.reset(t_ch)
        _rid.reset(t_rid)


def tekushchiy_rid() -> str:
    return _rid.get()


# ── Помощники логирования «каждой щели» ──────────────────────────────────────

def log_vhodyashchee(username, user_id, first_name, text: str) -> None:
    """Входящее действие пользователя: сообщение, команда или нажатие кнопки."""
    logger.info(f"👤 @{username or '—'} (id:{user_id}, {first_name or ''}) → {text}")


def log_tool_call(name: str, args: dict, naydeno: int) -> None:
    """Вызов инструмента агентом: имя + аргументы + сколько найдено."""
    logger.info(f"🔧 tool {name}({args}) → найдено: {naydeno}")


def log_vyzov_ii(model: str, latency_ms: int, usage: dict | None = None,
                 *, messages=None, otvet=None) -> None:
    """Вызов ИИ: модель + латентность (+ токены, если есть usage). В debug —
    добавляет промпт (messages) и ответ модели."""
    hvost = ""
    if usage:
        hvost = (f" · токены {usage.get('prompt_tokens', '?')}"
                 f"+{usage.get('completion_tokens', '?')}"
                 f"={usage.get('total_tokens', '?')}")
    logger.info(f"🧠 ИИ {model} · {latency_ms}мс{hvost}")
    if DEBUG_LOGS:
        if messages is not None:
            logger.debug(f"🧠 промпт: {messages}")
        if otvet is not None:
            logger.debug(f"🧠 ответ: {otvet}")


def log_ishodyashchee(username, text: str, *, ms: int | None = None, meta: str = "") -> None:
    """Исходящий ответ бота пользователю."""
    hvost = f" за {ms}мс" if ms is not None else ""
    logger.info(f"🤖 → @{username or '—'}{hvost}: {text}{(' ' + meta) if meta else ''}")


def log_oshibka(chto: str, *, zapros: str = "") -> None:
    """Ошибка обработки с полным контекстом: traceback (exc_info) + канал/rid
    (через фильтр) + исходный текст запроса. Звать из блока except."""
    hvost = f" | запрос: {zapros!r}" if zapros else ""
    logger.error(f"❌ {chto}{hvost}", exc_info=True)


# Обратная совместимость со старыми именами (использовались до этапа 26).
log_deystvie_polzovatelya = log_vhodyashchee


def log_otvet_bota(username, text: str) -> None:
    log_ishodyashchee(username, text)
