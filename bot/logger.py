# -*- coding: utf-8 -*-
"""Логирование на русском. Порт logger.ts (pino -> стандартный logging).

Требование CLAUDE.md: логировать КАЖДОЕ действие пользователя — входящие
сообщения, нажатия кнопок, команды. Каждая запись: дата/время, username,
user_id, first_name, текст ответа бота. Читаемо, по-русски.
"""
import logging
import sys

_FORMAT = "%(asctime)s  %(levelname)-5s  %(message)s"
_DATEFMT = "%d.%m.%Y %H:%M:%S"


def _setup() -> logging.Logger:
    lg = logging.getLogger("telewin")
    if lg.handlers:
        return lg
    # На Windows консоль по умолчанию CP866 -> кириллица кракозябрами.
    # Принудительно переводим stdout в UTF-8, чтобы лог читался в любой консоли.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    lg.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    lg.addHandler(h)
    lg.propagate = False
    return lg


logger = _setup()


def log_deystvie_polzovatelya(username, user_id, first_name, text: str) -> None:
    """Лог входящего действия: сообщение, команда или нажатие кнопки."""
    logger.info(f"👤 @{username or '—'} (id:{user_id}, {first_name or ''}) → {text}")


def log_otvet_bota(username, text: str) -> None:
    """Лог исходящего ответа бота пользователю."""
    logger.info(f"🤖 → @{username or '—'}: {text}")
